import json
import os
import re
from datetime import datetime
import time
import traceback
from typing import Literal, Union


class PrePostCheck:
    def __init__(self, sshobj, logpath, site_info, batch_id, phase_name):
        self.sshobj = sshobj
        self.logpath = logpath
        self.batch_id = batch_id
        self.site_info = site_info
        self.phase_name = phase_name
        self.node_name = self.site_info['node_name']
        self.action = site_info.get("Action", "")
        self.imsi = str(site_info["imsi"])
        print("20", self.imsi)
        self.apn = site_info["apn"]
        self.type = site_info["type"]
        self.gx_ip = site_info["current_gx_ip"]
        self.current_ro = self.site_info.get("current_ro_profile")
        self.proposed_ro = self.site_info.get("proposed_ro_profile")
        self.current_gx = self.site_info.get("current_gx_profile")
        self.proposed_gx = self.site_info.get("proposed_gx_profile")
        self.rule_space = self.site_info["rule_space"]

    def write_log(self, phase_name, checkpoint, output, method="w", subfolder=None):
        try:
            sitpath = f"{self.logpath}/{self.batch_id}/{phase_name}/{self.site_info['circle']}/{self.site_info['node_name']}/{self.action}"
            if subfolder:
                sitpath = f"{sitpath}/{subfolder}"
            os.makedirs(sitpath, exist_ok=True)
            with open(f"{sitpath}{os.sep}{checkpoint}.log", method) as log_file:
                log_file.write(output)
        except Exception as e:
            traceback.print_exc()
            return ""

    def fetch_log(self, phase_name, checkpoint, subfolder=None):
        try:
            sitpath = f"{self.logpath}/{self.batch_id}/{phase_name}/{self.site_info['circle']}/{self.site_info['node_name']}/{self.action}"
            if subfolder:
                sitpath = f"{sitpath}/{subfolder}"
            with open(f"{sitpath}{os.sep}{checkpoint}.log", "r") as log_file:
                return log_file.read()
        except Exception as e:
            traceback.print_exc()
            return ""
    def _exec_cmd(
        self,
        command,
        max_timeout=300,
        wait=5.0,
        prompt_patterns=None,
    ):
        try:
            if not hasattr(self.sshobj, "shell"):
                return "ERROR: sshobj.shell not initialized"

            shell = self.sshobj.shell

            if prompt_patterns is None:
                prompt_patterns = [b"#", b">", b"$"]

            MORE_PATTERNS = [
                b"--More--",
                b"(more)",
                b"---(more)---",
                b"Press any key",
            ]

            shell.send(" ")
            shell.send("paginate false" + "\n")
            shell.send(" ")
            end_time = time.time() + 2
            while shell.recv_ready() and time.time() < end_time:
                shell.recv(65535)

            shell.send(command + "\n")

            output = bytearray()
            start_time = time.time()

            last_data_time = time.time()
            got_output = False

            while True:
                if shell.recv_ready():
                    chunk = shell.recv(65535)
                    if chunk:
                        output.extend(chunk)

                        last_data_time = time.time()
                        got_output = True

                        if any(p in chunk for p in MORE_PATTERNS):
                            shell.send(" ")
                            continue

                        lines = output.splitlines()
                        if lines:
                            last_line = lines[-1].strip()
                            if any(last_line.endswith(p) for p in prompt_patterns):
                                break

                if time.time() - start_time > max_timeout:
                    output.extend(b"\n[WARN] Hard timeout reached\n")
                    break

                if not got_output:
                    if time.time() - last_data_time > wait:
                        output.extend(b"\n[WARN] No initial output\n")
                        break
                else:
                    if time.time() - last_data_time > (wait * 2):
                        output.extend(b"\n[WARN] Output stopped (silence timeout)\n")
                        break

                time.sleep(0.3)

            final = output.decode(errors="ignore")
            return final

        except Exception as e:
            return f"ERROR: {str(e)}"
        
    def is_valid(self, value):
        if value is None:
            return False
        value = str(value).strip().lower()
        return value not in ["", "null", "na", "n/a"]

    def to_list(self, value):
        """
        Convert comma-separated or slash-separated string or list to cleaned list.
        """
        if value is None:
            return []

        if isinstance(value, list):
            output = []
            for v in value:
                if v:
                    # Split by comma or slash
                    parts = re.split(r"[,/]", str(v).strip())
                    output.extend([p.strip() for p in parts if p.strip()])
            print("-------------list ", output)
            return output

        s = str(value).strip()
        if s.upper() in ("NA", "N/A", ""):
            return []

        # Split by comma or slash
        output = [p.strip() for p in re.split(r"[,/]", s) if p.strip()]
        print("-------------list ", output)
        return output
    
    def _clean_exec_output(
        self,
        output: str,
        cmd: str = "None",
        removing_exact_text=[],
        removing_text_startwith=[],
        return_type: Literal["str", "list"] = "list",
    ) -> Union[str, list]:
        exact_text = removing_exact_text or []
        start_with = tuple(removing_text_startwith) if removing_text_startwith else ()
        node_name = self.site_info['node_name']

        result = [
            line.strip()
            for line in output.splitlines()
            if line.strip()
            and not any(
                i in line for i in ("---", "{master}", f"{node_name}#", cmd)
            )
        ]

        if exact_text or start_with:
            result = [
                line.strip()
                for line in result
                if line not in exact_text and not line.startswith(start_with)
            ]

        if return_type == "str":
            return "\n".join(result)

        return result
    

    def _run_stats_command_with_retry(
        self,
        command,
        success_text,
        checkpoint,
        subfolder=None,
    ):
        command = str(command).strip()
        output = self._exec_cmd(command, wait=60)

        if success_text not in output.lower():
            retry_output = self._exec_cmd(command, wait=60)
            output = retry_output

        self.write_log(self.phase_name, checkpoint, output, subfolder=subfolder)
        return output

    def parse_kpi_log(self, log_text):
        """
        Extracts KPI name and the latest numeric value from KPI logs.
        Handles %, decimals, integers, and units like [Gbps], [ppm].
        """
        kpis = {}

        for line in log_text.splitlines():
            if (
                not line.strip()
                or line.startswith("=")
                or line.startswith("epg")
                or line.startswith("node-kpi")
            ):
                continue

            parts = line.strip().split()
            if len(parts) < 2:
                continue

            name = parts[0]
            idx = 1

            while idx < len(parts) and not any(
                ch.isdigit() or ch == "." for ch in parts[idx]
            ):
                name += " " + parts[idx]
                idx += 1

            if idx >= len(parts):
                continue

            raw_value = parts[idx].strip()

            if raw_value == "-" or raw_value == "":
                continue

            try:
                if raw_value.endswith("%"):
                    value = float(raw_value.replace("%", ""))
                elif "(" in raw_value and ")" in raw_value:
                    value = float(re.split(r"[%\(]", raw_value)[0])
                else:
                    value = float(raw_value)
            except ValueError:
                continue

            kpis[name.strip()] = value

        return kpis

    def compare_kpis(self, pre_kpis, post_kpis, failure_kpis=None):
        """
        Compare KPI values between pre and post logs.
        :param pre_kpis: dict of pre-check KPI values
        :param post_kpis: dict of post-check KPI values
        :param failure_kpis: list of KPI names where higher value is worse
        :return: tuple (status, degraded_dict)
        """
        if failure_kpis is None:
            failure_kpis = [
                "FR",
                "DropR",
                "PktDrop",
                "Failure",
                "Drop",
            ]

        degraded = {}

        for kpi, pre_val in pre_kpis.items():
            post_val = post_kpis.get(kpi)
            if post_val is None:
                continue

            is_failure_kpi = any(word in kpi for word in failure_kpis)

            if is_failure_kpi:
                if post_val > pre_val:
                    degraded[kpi] = f"Degraded: Pre={pre_val}, Post={post_val}"
            else:
                if post_val < pre_val:
                    degraded[kpi] = f"Degraded: Pre={pre_val}, Post={post_val}"

        if degraded:
            return False, degraded
        else:
            return True, "All KPIs OK"

    def extract_ro_das_value(self, log_text):
        match = re.search(r"diameter-application-system\s+(\S+)", log_text)
        if match:
            das_value = match.group(1)
            return das_value
        else:
            return None

    def extract_das_value(self, log_text):
        """
        Extracts the diameter-application-system value from the log.
        Example: 'diameter-application-system       DAS-Gx-Jaipur-SAPC-03'
        Returns: 'DAS-Gx-Jaipur-SAPC-03' or None if not found
        """
        match = re.search(r"\bdiameter-application-system\s+(\S+)", log_text)
        if match:
            return match.group(1)
        return None


    def extract_peer_value(self, log_text):
        """
        Extract all peer values from the log.
        Returns: list of peer names (unique, order preserved)
        """
        peers = re.findall(r"\bpeer\s+(\S+)", log_text)
        return list(dict.fromkeys(peers))  # remove duplicates, keep order

    def to_check_date(self):
        try:
            output = self._exec_cmd("show clock\r", wait=30).strip()
            self.write_log(self.phase_name, "date", output)
            print("---------------------------------------------------------")
            print(output)
            print("---------------------------------------------------------")

            if "clock" in output.lower():
                self.site_info["status"] = True
                self.site_info["output"] = f"Date fetched successfully"
            else:
                self.site_info["status"] = False
                self.site_info["output"] = f"Date mismatch! Remote "
            return self.site_info["status"], self.site_info["output"]
        except Exception as e:
            self.write_log(self.phase_name, "date", f"Error: {e}")
            traceback.print_exc()
            return False, f"Error occurred in fetching date: {e}"

    def to_check_backup_manager(self):
        try:
            today_date = datetime.now().strftime("%Y%m%d")
            phase_prefix = "Pre" if "precheck" in self.phase_name.lower() else "Post"
            backup_name = f"{phase_prefix}_{self.node_name}_{today_date}"
            command = (
                f"brm backup-manager configuration-system create-backup name {backup_name}\r"
            )
            output = self._exec_cmd(command, wait=60)
            self.write_log(self.phase_name, "Backup_Manager", output)

            if "command not found" in output.lower() or "error" in output.lower():
                self.site_info["status"] = False
                self.site_info["output"] = f"Failed to create backup {backup_name}"
            else:
                self.site_info["status"] = True
                self.site_info["output"] = f"Backup successfully: {backup_name}"

            return self.site_info["status"], self.site_info["output"]
        except Exception as e:
            self.write_log(self.phase_name, "Backup_Manager", f"Error: {e}")
            traceback.print_exc()
            return False, f"Error occurred while creating backup: {e}"

    def to_export_current_config(self):
        try:
            command = "show running-config | nomore\r"
            output = self._exec_cmd(command, wait=60)
            if "precheck" in self.phase_name:
                output = "Precheck:" + output
            elif "postcheck" in self.phase_name:
                output = "postcheck:" + output
            self.write_log(self.phase_name, "Export_Config", output)
            if output:
                self.site_info["status"] = True
                self.site_info["output"] = "Current configuration exported successfully"
            else:
                self.site_info["status"] = False
                self.site_info["output"] = "Failed to export configuration"
            return self.site_info["status"], self.site_info["output"]
        except Exception as e:
            self.write_log(self.phase_name, "Export_Config", f"Error: {e}")
            traceback.print_exc()
            return False, f"Error occurred while exporting configuration: {e}"

    def to_check_active_alarms(self):
        try:
            command = "show fm alarm\r"
            output = self._exec_cmd(command=command,wait=300)
            self.write_log(self.phase_name, "Active_Alarms", output)
            print(output)
            if "CRITICAL" not in output:
                self.site_info["status"] = True
                self.site_info["output"] = "Active alarms checked successfully"
            else:
                self.site_info["status"] = False
                self.site_info["output"] = "Alarms present or could not check"
            return self.site_info["status"], self.site_info["output"]
        except Exception as e:
            self.write_log(self.phase_name, "Active_Alarms", f"Error: {e}")
            traceback.print_exc()
            return False, f"Error occurred while checking active alarms: {e}"

    def to_check_node_kpi(self):
        try:
            command = "epg node kpi\r"
            output = self._exec_cmd(command, wait=60)
            self.write_log(self.phase_name, "Node_KPI", output)

            if "postcheck" in self.phase_name:
                prechek_logs = self.fetch_log("precheck", "Node_KPI")
                postcheck_log = output
                print("------------------precheck logs-------------------------")
                print(prechek_logs)
                print("------------------postcheck logs-------------------------")
                print(postcheck_log)
                pre_kpis = self.parse_kpi_log(prechek_logs)
                post_kpis = self.parse_kpi_log(postcheck_log)
                status, result = self.compare_kpis(pre_kpis, post_kpis)
                print("------------------result logs-------------------------")
                print(result, status)
                if status:
                    self.site_info["status"] = True
                    self.site_info["output"] = "Node KPI fetched successfully"
                else:
                    self.site_info["status"] = False
                    self.site_info["output"] = (
                        "Failed to fetch Node KPI values are degraded"
                    )
            else:
                if "kpi" in output.lower():
                    self.site_info["status"] = True
                    self.site_info["output"] = "Node KPI fetched successfully"
                else:
                    self.site_info["status"] = False
                    self.site_info["output"] = "Failed to fetch Node KPI"

            return self.site_info["status"], self.site_info["output"]
        except Exception as e:
            self.write_log(self.phase_name, "Node_KPI", f"Error: {e}")
            traceback.print_exc()
            return False, f"Error occurred while fetching Node KPI: {e}"

    def to_check_total_user_category(self):
        try:
            command = (
                f"show running-config epg pgw apn {self.apn} user-category | nomore\r"
            )
            output = self._exec_cmd(command=command,wait=60)
            self.write_log(
                self.phase_name, "Total_User_Category", output, subfolder=self.imsi
            )
            (self.phase_name, "--------------------------phase name------------------")
            if "user-category" in output:
                self.site_info["status"] = True
                self.site_info["output"] = "Total user category fetched successfully"
            else:
                self.site_info["status"] = False
                self.site_info["output"] = "Failed to fetch user category"
            return self.site_info["status"], self.site_info["output"]
        except Exception as e:
            self.write_log(
                self.phase_name,
                "Total_User_Category",
                f"Error: {e}",
                subfolder=self.imsi,
            )
            traceback.print_exc()
            return False, f"Error occurred while fetching user category: {e}"

    def to_check_imsi(self):
        try:
            action = str(self.action).lower()
            imsi_value = str(self.imsi)
            print("484", imsi_value)

            expected_present = True
            if "precheck" in self.phase_name:
                expected_present = False if "creation" in action else True
            elif "postcheck" in self.phase_name:
                expected_present = False if "deletion" in action else True

            if not self.is_valid(imsi_value):
                self.site_info["status"] = False
                self.site_info["output"] = (
                    "IMSI value not found in Inventory as expected"
                )
                return self.site_info["status"], self.site_info["output"]

            command = (
                f"show running-config epg pgw apn {self.apn} user-category |nomore\r"
            )
            output = self._exec_cmd(command=command,wait=60)
            self.write_log(self.phase_name, "IMSI_Check", output, subfolder=self.imsi)

            output = self._clean_exec_output(output, command, return_type="str")
            found = imsi_value.lower() in output.lower()
            print("507", found,)

            if expected_present and found:
                self.site_info["status"] = True
                self.site_info["output"] = "IMSI present as expected"
            elif (not expected_present) and (not found):
                self.site_info["status"] = True
                self.site_info["output"] = "IMSI not present as expected"
            elif expected_present and (not found):
                self.site_info["status"] = False
                self.site_info["output"] = "IMSI not present but expected"
            else:
                self.site_info["status"] = False
                self.site_info["output"] = "IMSI present"
            return self.site_info["status"], self.site_info["output"]

        except Exception as e:
            self.write_log(
                self.phase_name, "IMSI_Check", f"Error: {e}", subfolder=self.imsi
            )
            traceback.print_exc()
            return False, f"Error occurred while checking IMSI: {e}"

    def to_check_gx_profile(self):
        try:
            gx = self.pick_value(self.current_gx, self.proposed_gx)
            command = f"show running-config epg pgw apn {self.apn} service-based-charging policy-control dynamic gx-profile {gx}\r"
            output = self._exec_cmd(command=command,wait=60)
            self.write_log(self.phase_name, "Gx_Profile", output, subfolder=self.imsi)
            print("------------------------------------------")
            print(output)
            print(self.apn, "--------------------apn---------------------")
            action = str(self.action).lower()
            current_pcscf_pool = self.site_info.get("current_pcscf_pool")
            proposed_pcscf_pool = self.site_info.get("proposed_pcscf_pool")
            pcscf_pool = self.pick_value(current_pcscf_pool, proposed_pcscf_pool)
            if not self.is_valid(gx):
                self.site_info["status"] =  action in  ("deletion","modification")
                if pcscf_pool:
                    self.site_info["status"] = True
                if action in  ("deletion","modification"):
                    self.site_info["output"] = (
                        "Gx profile value not found in Inventory MOP"
                    )
                else:
                    self.site_info["output"] = (
                        "Gx profile value not found in Inventory MOP but expected"
                    )
                return self.site_info["status"], self.site_info["output"]

            expected_present = not (
                "postcheck" in self.phase_name and "deletion" in action
            )

            cleaned_output = self._clean_exec_output(output, command, return_type="str")
            found = "diameter-application-system" in cleaned_output.lower()

            if found:
                self.site_info["status"] = True
                self.site_info["output"] = "Gx profile present"
            else:
                self.site_info["status"] = False
                self.site_info["output"] = "Gx profile not present"
            return self.site_info["status"], self.site_info["output"]
        except Exception as e:
            self.write_log(
                self.phase_name, "Gx_Profile", f"Error: {e}", subfolder=self.imsi
            )
            traceback.print_exc()
            return False, f"Error occurred while verifying Gx profile: {e}"

    def to_check_gx_diameter_application_system(self):
        try:
            logs = self.fetch_log(self.phase_name, "Gx_Profile", subfolder=self.imsi)
            das_value = self.extract_das_value(logs)
            action = str(self.action).lower()

            expected_present = not (
                "postcheck" in self.phase_name and "deletion" in action
            )

            if not self.is_valid(das_value):
                self.site_info["status"] = (
                    True if action in  ("deletion","modification") else not expected_present
                )
                if action in  ("deletion","modification"):
                    self.site_info["output"] = (
                        "Gx Diameter Application System value not found in Inventory MOP as expected for deletion"
                    )
                elif expected_present:
                    self.site_info["output"] = (
                        "Gx Diameter Application System value not found in Inventory MOP but expected"
                    )
                else:
                    self.site_info["output"] = (
                        "Gx Diameter Application System value not found in Inventory MOP as expected"
                    )
                return self.site_info["status"], self.site_info["output"]

            command = f"show running-config epg pgw diameter diameter-application-system {das_value}\r"
            output = self._exec_cmd(command=command,wait=60)
            self.write_log(
                self.phase_name,
                "Gx_Diameter_Application_System",
                output,
                subfolder=self.imsi,
            )

            output = self._clean_exec_output(output, command, return_type="str")
            found = "peer " in output.lower()
            if found:
                self.site_info["status"] = True
                self.site_info["output"] = "Gx Diameter Application System present"
            else:
                self.site_info["status"] = False
                self.site_info["output"] = "Gx Diameter Application System not present"
            return self.site_info["status"], self.site_info["output"]
        except Exception as e:
            self.write_log(
                self.phase_name,
                "Gx_Diameter_Application_System",
                f"Error: {e}",
                subfolder=self.imsi,
            )
            traceback.print_exc()
            return (
                False,
                f"Error occurred while verifying Gx Diameter Application System: {e}",
            )

    def to_check_gx_peer(self):
        try:
            logs = self.fetch_log(
                self.phase_name, "Gx_Diameter_Application_System", subfolder=self.imsi
            )
            peer_value = self.extract_peer_value(logs)
            action = str(self.action).lower()

            expected_present = not (
                "postcheck" in self.phase_name and action in  ("deletion","modification")
            )

            if not peer_value:
                self.site_info["status"] = (
                    True if "deletion" in action else not expected_present
                )
                if action in  ("deletion","modification"):
                    self.site_info["output"] = (
                        "Gx peer value not found in Inventory MOP as expected for deletion"
                    )
                elif expected_present:
                    self.site_info["output"] = (
                        "Gx peer value not found in Inventory MOP but expected"
                    )
                else:
                    self.site_info["output"] = (
                        "Gx peer value not found in Inventory MOP as expected"
                    )
                return self.site_info["status"], self.site_info["output"]
            output = ""
            for peer in peer_value:
                command = f"show running-config epg pgw diameter peer {peer}\r"
                output += self._exec_cmd(command, wait=10)
            self.write_log(self.phase_name, "Gx_Peer", output, subfolder=self.imsi)

            output = self._clean_exec_output(output, command, return_type="str")
            gx_ip = self.pick_value(self.site_info["current_gx_ip"],self.site_info["proposed_gx_ip"])
            gx_ips = self.to_list(gx_ip)
            if not gx_ips:
                self.site_info["status"] = False
                self.site_info["output"] = f"Gx ip not found in Mop"
                return self.site_info["status"], self.site_info["output"]
            
            found = ""
            not_found = ""
            for ip in gx_ips :
                flag = ip.lower() in output.lower()
                if flag:
                    found += ip
                else:
                    not_found += ip

            if not not_found:
                self.site_info["status"] = True
                self.site_info["output"] = f"Gx peer {gx_ip} present"
            else:
                self.site_info["status"] = False
                self.site_info["output"] = f"Gx peer {not_found} not present"
            return self.site_info["status"], self.site_info["output"]
        except Exception as e:
            self.write_log(
                self.phase_name, "Gx_Peer", f"Error: {e}", subfolder=self.imsi
            )
            traceback.print_exc()
            return False, f"Error occurred while verifying Gx peer: {e}"

    def to_check_ro_profile(self):
        try:
            ro_profile = self.pick_value(self.current_ro, self.proposed_ro)
            action = str(self.action).lower()

            if not self.is_valid(ro_profile):
                self.site_info["status"] = action in  ("deletion","modification")
                if action in  ("deletion","modification"):
                    self.site_info["output"] = (
                        "Ro profile value not found in Inventory MOP as expected for deletion"
                    )
                else:
                    self.site_info["output"] = (
                        "Ro profile value not found in Inventory MOP but expected"
                    )
                return self.site_info["status"], self.site_info["output"]

            command = f"show running-config epg pgw apn www service-based-charging credit-control ro-profile {ro_profile}\r"
            output = self._exec_cmd(command=command,wait=60)
            self.write_log(self.phase_name, "Ro_Profile", output, subfolder=self.imsi)
            print("command", command)
            print("output", output)

            output = self._clean_exec_output(output, command, return_type="str")
            found = "service-context-id " in output.lower()
            if found:
                self.site_info["status"] = True
                self.site_info["output"] = "Ro profile present"
            else:
                self.site_info["status"] = False
                self.site_info["output"] = "Ro profile not present"
            return self.site_info["status"], self.site_info["output"]
        except Exception as e:
            self.write_log(
                self.phase_name, "Ro_Profile", f"Error: {e}", subfolder=self.imsi
            )
            traceback.print_exc()
            return False, f"Error occurred while verifying Ro profile: {e}"

    def to_check_ro_diameter_application_system(self):
        try:
            log_text = self.fetch_log(
                self.phase_name, "Ro_Profile", subfolder=self.imsi
            )
            das_value = self.extract_ro_das_value(log_text)
            action = str(self.action).lower()

            expected_present = not (
                "postcheck" in self.phase_name and "deletion" in action
            )

            if not self.is_valid(das_value):
                self.site_info["status"] = (
                    True if action in  ("deletion","modification") else not expected_present
                )
                if action in  ("deletion","modification"):
                    self.site_info["output"] = (
                        "Ro Diameter Application System value not found in ro profile log as expected for deletion"
                    )
                elif expected_present:
                    self.site_info["output"] = (
                        "Ro Diameter Application System value not found in ro profile log but expected"
                    )
                else:
                    self.site_info["output"] = (
                        "Ro Diameter Application System value not found in ro profile log as expected"
                    )
                return self.site_info["status"], self.site_info["output"]

            command = f"show running-config epg pgw diameter diameter-application-system {das_value} | nomore\r"
            output = self._exec_cmd(command=command,wait=60)
            self.write_log(
                self.phase_name,
                "Ro_Diameter_Application_System",
                output,
                subfolder=self.imsi,
            )

            output = self._clean_exec_output(output, command, return_type="str")
            found = "application-id" in output.lower()
            if found:
                self.site_info["status"] = True
                self.site_info["output"] = "Ro Diameter Application System present"
            else:
                self.site_info["status"] = False
                self.site_info["output"] = "Ro Diameter Application System not present"
            return self.site_info["status"], self.site_info["output"]
        except Exception as e:
            self.write_log(
                self.phase_name,
                "Ro_Diameter_Application_System",
                f"Error: {e}",
                subfolder=self.imsi,
            )
            traceback.print_exc()
            return (
                False,
                f"Error occurred while verifying Ro Diameter Application System: {e}",
            )

    def to_check_rule_space(self):
        try:
            action = str(self.action).lower()

            if not self.is_valid(self.rule_space):
                self.site_info["status"] = action in  ("deletion","modification")
                if action in  ("deletion","modification"):
                    self.site_info["output"] = (
                        "Rule-space value not found in Inventory MOP as expected for deletion"
                    )
                else:
                    self.site_info["output"] = (
                        "Rule-space value not found in Inventory MOP but expected"
                    )
                return self.site_info["status"], self.site_info["output"]

            command = (
                f"show running-config epg pgw rule-space {self.rule_space} | nomore\r"
            )
            output = self._exec_cmd(command=command,wait=60)
            self.write_log(self.phase_name, "Rule_Space", output, subfolder=self.imsi)

            output = self._clean_exec_output(output, command, return_type="str")
            found = "enable-access-control-rules" in output.lower()
            if found:
                self.site_info["status"] = True
                self.site_info["output"] = f"Rule-space {self.rule_space} present"
            else:
                self.site_info["status"] = False
                self.site_info["output"] = f"Rule-space {self.rule_space} not present"

            return self.site_info["status"], self.site_info["output"]

        except Exception as e:
            self.write_log(
                self.phase_name, "Rule_Space", f"Error: {e}", subfolder=self.imsi
            )
            traceback.print_exc()
            return False, f"Error occurred while verifying Rule-space: {e}"

    def pick_value(self, current, proposed):
        action = str(self.action).lower()
        is_precheck = "precheck" in self.phase_name.lower()

        if "creation" in action:
            return proposed

        elif "deletion" in action:
            return current

        elif "modification" in action:
            return current if is_precheck else proposed

        return ""

    def to_check_s6b_profile(self):
        try:
            current_s6b_profile = self.site_info.get("current_s6b_profile")
            proposed_s6b_profile = self.site_info.get("proposed_s6b_profile")
            s6b_profile = self.pick_value(current_s6b_profile, proposed_s6b_profile)

            if not self.is_valid(s6b_profile):
                self.site_info["status"] = True
                self.site_info["output"] = "S6B profile value not found in Inventory"
                return self.site_info["status"], self.site_info["output"]

            command = f"show running-config epg pgw aaa s6b-profile {s6b_profile}\r"
            output = self._exec_cmd(command=command,wait=60)
            self.write_log(self.phase_name, "S6b_Profile", output, subfolder=self.imsi)
            action = str(self.action).lower()

            expected_present = True
            if "precheck" == self.phase_name:
                expected_present = False if "creation" in action else True
            elif "postcheck" in self.phase_name:
                expected_present = False if "deletion" in action else True

            output = self._clean_exec_output(output, command, return_type="str")
            found = s6b_profile.lower() in output.lower()

            if expected_present and found:
                self.site_info["status"] = True
                self.site_info["output"] = "S6B profile present"
            elif (not expected_present) and (not found):
                self.site_info["status"] = False
                self.site_info["output"] = "S6B profile not present"
            elif expected_present and (not found):
                self.site_info["status"] = False
                self.site_info["output"] = "S6B profile not present"
            else:
                self.site_info["status"] = True
                self.site_info["output"] = "S6B profile present"
            return self.site_info["status"], self.site_info["output"]
        except Exception as e:
            self.write_log(
                self.phase_name, "S6b_Profile", f"Error: {e}", subfolder=self.imsi
            )
            traceback.print_exc()
            return False, f"Error occurred while checking S6B profile: {e}"

    def to_check_pcscf_ip_pool(self):
        try:
            command = "show running-config epg pgw p-cscf-ip-pool | nomore\r"
            output = self._exec_cmd(command=command,wait=60)
            self.write_log(self.phase_name, "PCSCF_IP_Pool", output)
            current_pcscf_pool = self.site_info.get("current_pcscf_pool")
            proposed_pcscf_pool = self.site_info.get("proposed_pcscf_pool")
            action = str(self.action).lower()
            pcscf_pool = self.pick_value(current_pcscf_pool, proposed_pcscf_pool)

            if not self.is_valid(pcscf_pool):
                self.site_info["status"] = True
                self.site_info["output"] = "P-CSCF IP pool value missing in site_info"
                return self.site_info["status"], self.site_info["output"]

            expected_present = True
            if "precheck" in self.phase_name:
                expected_present = False if "creation" in action else True
            elif "postcheck" in self.phase_name:
                expected_present = False if "deletion" in action else True
            output = self._clean_exec_output(output, command, return_type="str")
            found = pcscf_pool.lower() in output.lower()
            if expected_present and found:
                self.site_info["status"] = True
                self.site_info["output"] = "P-CSCF IP pool present"
            elif (not expected_present) and (not found):
                self.site_info["status"] = False
                self.site_info["output"] = "P-CSCF IP pool not present"
            elif expected_present and (not found):
                self.site_info["status"] = False
                self.site_info["output"] = "P-CSCF IP pool not present"
            else:
                self.site_info["status"] = True
                self.site_info["output"] = "P-CSCF IP pool present "
            return self.site_info["status"], self.site_info["output"]

        except Exception as e:
            self.write_log(self.phase_name, "PCSCF_IP_Pool", f"Error: {e}")
            traceback.print_exc()
            return False, f"Error occurred while checking P-CSCF IP pool: {e}"

    def to_check_pcscf_category_configuration(self):
        try:
            command = f"show running-config epg pgw apn {self.apn} p-cscf category\r"
            output = self._exec_cmd(command=command,wait=60)
            self.write_log(
                self.phase_name, "PCSCF_Category", output, subfolder=self.imsi
            )
            action = str(self.action).lower()
            imsi_value = str(self.imsi)
            crt_pool = self.site_info["current_pcscf_pool"]
            pro_pool = self.site_info["proposed_pcscf_pool"]
            pool = self.pick_value(crt_pool,pro_pool)
            if not self.is_valid(imsi_value):
                self.site_info["status"] = False
                self.site_info["output"] = "IMSI value missing in mop"
                return self.site_info["status"], self.site_info["output"]
            if not self.is_valid(pool):
                self.site_info["status"] = True
                self.site_info["output"] = "pcscf_pool value missing in mop"
                return self.site_info["status"], self.site_info["output"]

            expected_present = True
            if "precheck" in self.phase_name:
                expected_present = False if "creation" in action else True
            elif "postcheck" in self.phase_name:
                expected_present = False if "deletion" in action else True
            output = self._clean_exec_output(output, command, return_type="str")
            found = imsi_value.lower() in output.lower()
            if expected_present and found:
                self.site_info["status"] = True
                self.site_info["output"] = "IMSI present in P-CSCF category as expected"
            elif (not expected_present) and (not found):
                self.site_info["status"] = True
                self.site_info["output"] = (
                    "IMSI not present in P-CSCF category as expected"
                )
            elif expected_present and (not found):
                self.site_info["status"] = False
                self.site_info["output"] = (
                    "IMSI not present in P-CSCF category but expected"
                )
            else:
                self.site_info["status"] = False
                self.site_info["output"] = "IMSI present in P-CSCF category "
            return self.site_info["status"], self.site_info["output"]
        except Exception as e:
            self.write_log(
                self.phase_name, "PCSCF_Category", f"Error: {e}", subfolder=self.imsi
            )
            traceback.print_exc()
            return (
                False,
                f"Error occurred while checking P-CSCF category configuration: {e}",
            )
    
    def to_check_apn_statistics(self):
        try:
            command = f"epg pgw apn {self.apn} statistics".strip()
            output = self._run_stats_command_with_retry(
                command,
                "apn-statistics",
                "APN_Statistics",
                subfolder=self.imsi,
            )
            if "apn-statistics" in output.lower():
                self.site_info["status"] = True
                self.site_info["output"] = "APN statistics fetched successfully"
            else:
                self.site_info["status"] = False
                self.site_info["output"] = "Failed to fetch APN statistics"
            return self.site_info["status"], self.site_info["output"]
        except Exception as e:
            self.write_log(
                self.phase_name, "APN_Statistics", f"Error: {e}", subfolder=self.imsi
            )
            traceback.print_exc()
            return False, f"Error occurred while checking APN statistics: {e}"

    def to_check_pcrf_statistics(self):
        try:
            command = "epg pgw statistics-pcrf"
            output = self._run_stats_command_with_retry(
                command,
                "pgw-pcrf-statistics",
                "PCRF_Statistics",
            )
            if "pgw-pcrf-statistics" in output.lower():
                self.site_info["status"] = True
                self.site_info["output"] = "PCRF statistics fetched successfully"
            else:
                self.site_info["status"] = False
                self.site_info["output"] = "Failed to fetch PCRF statistics"
            return self.site_info["status"], self.site_info["output"]
        except Exception as e:
            self.write_log(self.phase_name, "PCRF_Statistics", f"Error: {e}")
            traceback.print_exc()
            return False, f"Error occurred while checking PCRF statistics: {e}"

    def to_check_pcscf_statistics(self):
        try:
            command = "epg pgw statistics-pcscf"
            output = self._run_stats_command_with_retry(
                command,
                "pgw-pcscf-statistics",
                "PCSCF_Statistics",
            )
            if "pgw-pcscf-statistics" in output.lower():
                self.site_info["status"] = True
                self.site_info["output"] = "PCSCF statistics fetched successfully"
            else:
                self.site_info["status"] = False
                self.site_info["output"] = "Failed to fetch PCSCF statistics"
            return self.site_info["status"], self.site_info["output"]
        except Exception as e:
            self.write_log(self.phase_name, "PCSCF_Statistics", f"Error: {e}")
            traceback.print_exc()
            return False, f"Error occurred while checking PCSCF statistics: {e}"
