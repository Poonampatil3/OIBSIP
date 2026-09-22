import sys
import os
import time
import logging
import re
import json
import datetime
import traceback
import inspect
import difflib
import sys
from datetime import datetime
import time





logger = logging.getLogger(__name__)

class MMECAP_PREPOST:
    def __init__(self, site_info, sshobj, logpath, batch_id, phase_name):
        self.site_info = site_info
        self.sshobj = sshobj
        self.logpath = logpath
        self.batch_id = batch_id
        self.phase_name = phase_name
        self.circle = site_info.get("circle")
        self.node = site_info.get("node_name")
        self.node_ip = site_info.get("node_ip")
        self.oss_name = site_info.get("oss_name")
        self.oss_ip = site_info.get("oss_ip")
        self.mme = site_info.get("mme")
        self.current_rmc = site_info.get("current_rmc")
        self.proposed_rmc = site_info.get("proposed_rmc")


    # -------------------- Logging -------------------- #
    def write_log(self, phase_name, Check, output, method="a"):

        sitpath = f"{self.logpath}/{self.batch_id}/{phase_name}/{self.site_info['circle']}/{self.node}"
        try:
            os.makedirs(sitpath, exist_ok=True)
        except Exception:
            pass

        # Fallback to phase_name if Check is None or empty
        if not Check:
            Check = phase_name

        logfile = f"{sitpath}{os.sep}{Check}.log"

        try:
            if isinstance(output, (dict, list)):
                output_str = json.dumps(output, indent=2)
            elif isinstance(output, bytes):
                try:
                    output_str = output.decode("utf-8", errors="ignore")
                except Exception:
                    output_str = str(output)
            elif output is None:
                output_str = "<NO OUTPUT>"
            else:
                output_str = str(output)
        except Exception:
            output_str = "<UNSERIALIZABLE OUTPUT>"

        try:
            with open(logfile, method, encoding="utf-8") as log_file:
                log_file.write(output_str + "\n\n")
        except Exception as e:
            logger.error(f"Failed to write log '{logfile}': {e}")
            
    def fetch_log(self, phase_name, checkpoint=None):
    
        sitpath = f"{self.logpath}/{self.batch_id}/{phase_name}/{self.site_info['circle']}/{self.node}"

        # Use caller function name if checkpoint is not given
        if not checkpoint:
            checkpoint = inspect.stack()[1].function

        logfile = f"{sitpath}{os.sep}{checkpoint}.log"
        if not os.path.exists(logfile):
            return f"[ERROR] Log file not found: {logfile}"

        try:
            with open(logfile, "r", encoding="utf-8") as log_file:
                return log_file.read()
        except Exception as e:
            logger.error(f"Failed to read log {logfile}: {e}")
            return f"[ERROR] Failed to read log: {e}"

    # -----------------------------
    # Node Unique ID
    # -----------------------------
    def parse_kpi_log(self, log_text):

        kpis = {}

        for line in log_text.splitlines():
            # Ignore headers, empty lines, and section titles
            if not line.strip() or line.startswith("=") or line.startswith("epg") or line.startswith("node-kpi"):
                continue

            parts = line.strip().split()
            if len(parts) < 2:
                continue

            name = parts[0]
            idx = 1

            # Capture KPI name with units until a numeric value is found
            while idx < len(parts) and not any(ch.isdigit() or ch == "." for ch in parts[idx]):
                name += " " + parts[idx]
                idx += 1

            if idx >= len(parts):
                continue

            raw_value = parts[idx].strip()

            # Skip invalid or missing values
            if raw_value == "-" or raw_value == "":
                continue

            try:
                # Handle % values
                if raw_value.endswith("%"):
                    value = float(raw_value.replace("%", ""))
                # Handle values with parentheses (e.g. 3%(14%))
                elif "(" in raw_value and ")" in raw_value:
                    value = float(re.split(r"[%\(]", raw_value)[0])
                else:
                    value = float(raw_value)
            except ValueError:
                continue

            kpis[name.strip()] = value

        return kpis

    def compare_kpis(self, pre_kpis, post_kpis, failure_kpis=None):

        if failure_kpis is None:
            failure_kpis = [
                "FR", "DropR", "PktDrop", "Failure", "Drop"  # generic failure KPI keywords
            ]

        degraded = {}

        for kpi, pre_val in pre_kpis.items():
            post_val = post_kpis.get(kpi)
            if post_val is None:
                continue

            # Determine degradation rule
            is_failure_kpi = any(word in kpi for word in failure_kpis)

            if is_failure_kpi:
                # Higher value → degraded
                if post_val > pre_val:
                    degraded[kpi] = f"Degraded: Pre={pre_val}, Post={post_val}"
            else:
                # Lower value → degraded
                if post_val < pre_val:
                    degraded[kpi] = f"Degraded: Pre={pre_val}, Post={post_val}"

        if degraded:
            return False, degraded  # NOK — degradation detected
        else:
            return True, "All KPIs OK"

    # ------------------------------------- Checkpoints ------------------------------------------- #


##################################### CheckPoint 1ST #### #######################################
# CheckPoint 1 : Date Check
    def check_date(self):
        
        cmd = "date"
        
        try:
            #output = self.sshobj.exec_cmd("date", wait=30, EOL='#').strip()
            output = self.sshobj.exec_retry(cmd, wait=30, EOL="#")
            self.write_log(self.phase_name, "date", output)

            date_line = None
            for line in output.splitlines():
                line = line.strip()
                if re.match(r"^(Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s", line):
                    date_line = line
                    break

            if not date_line:
                self.site_info['status'] = False
                self.site_info['output'] = "Date command executed but date not found"
            else:
                self.site_info['status'] = True
                self.site_info['output'] = f"Date fetched successfully: {date_line}"

            return self.site_info['status'], self.site_info['output']

        except Exception as e:
            self.write_log(self.phase_name, "date", f"Error: {e}")
            traceback.print_exc()
            return False, f"Error occurred in fetching date: {e}"


    ### CheckPoint 2ND ####

    def export_current_config(self, mode="pre"):
    
        cmd = "gsh export_config_active"

        try:
            output = self.sshobj.exec_retry(cmd, wait=30, EOL="#")
            self.write_log(self.phase_name, "export_config_active", output)
        except Exception as e:
            return False, f"ERROR: Failed to execute export command: {e}"

        export_path = None
        for line in output.splitlines():
            line = line.strip()
            if line.startswith("Exported result:"):
                export_path = line.replace("Exported result:", "").strip()
                break

        if not export_path:
            return False, "ERROR: Exported result not found — config export failed."

        # -------- PRECHECK --------
        if mode.lower() == "pre":
            return True, f"Config exported at: {export_path}"

        # -------- POSTCHECK --------
        if mode.lower() == "post":
            return True, f"Config exported at: {export_path}"

        return False, "ERROR: Invalid mode (use 'pre' or 'post')"



  ##########################  ### CheckPoint 3RD #### ########################################
  
    def check_current_rmc_value(self, mode="pre"):

        # -------- Determine expected RMC --------
        if mode.lower() == "pre":
            expected_rmc = self.site_info.get("current_rmc")
            validation_text = "Current RMC value verification"
        elif mode.lower() == "post":
            expected_rmc = self.site_info.get("proposed_rmc")
            validation_text = "Proposed RMC value verification"
        else:
            return False, "Invalid execution mode. Supported values are 'pre' and 'post'."

        if expected_rmc is None:
            return False, (
                f"{validation_text} failed. "
                f"Expected RMC value is not available in the input data."
            )

        expected_rmc = int(expected_rmc)

        cmd = "gsh get_ne | grep rmc"

        try:
            raw_output = self.sshobj.exec_retry(cmd, wait=30, EOL="#")

            # Clean prompt lines
            cleaned_output = "\n".join(
                line for line in raw_output.splitlines()
                if not line.strip().startswith("===")
            )

            # Log command and output
            self.write_log(
                self.phase_name,
                "check_rmc",
                f"Command Executed:\n{cmd}\n\nCommand Output:\n{cleaned_output}"
            )

        except Exception as e:
            return False, f"Failed to execute RMC verification command. Error: {e}"

        # -------- Parse RMC value --------
        match = re.search(r"rmc\s*\(.*?\)\s+(\d+)", cleaned_output, re.IGNORECASE)
        if not match:
            return False, (
                f"{validation_text} failed. "
                f"RMC value could not be determined from node output.\n"
                f"Output:\n{cleaned_output}"
            )

        current_rmc = int(match.group(1))

        # Store PRE value if needed later
        if mode.lower() == "pre":
            self.pre_rmc_value = current_rmc

        # -------- Validation --------
        if current_rmc == expected_rmc:
            return True, (
                f"{validation_text} completed successfully.\n"
                f"Expected RMC value : {expected_rmc}\n"
                f"Observed RMC value : {current_rmc}"
            )

        return False, (
            f"{validation_text} failed.\n"
            f"Expected RMC value : {expected_rmc}\n"
            f"Observed RMC value : {current_rmc}"
        )

 ########################   ### CheckPoint 4TH ####   ############################################
    

    def check_alarms_in_node(self):
       
        try:
            mode = self.site_info.get("mode", "pre").lower()
            phase_label = "Alarm verification"

            cmd = "gsh list_alarms"
            raw_output = self.sshobj.exec_retry(cmd, wait=30, EOL="#")

            cleaned_lines = [
                line.strip()
                for line in raw_output.splitlines()
                if line.strip() and not line.strip().startswith("===")
            ]

            self.write_log(
                self.phase_name,
                "list_alarms",
                f"Command:\n{cmd}\n\nOutput:\n" + "\n".join(cleaned_lines)
            )

            # ---------------- PARSE ALARMS ----------------
            severity_count = {}
            critical_alarms = []

            for line in cleaned_lines:
                # Typical alarm format contains severity column
                # Example: major / critical / minor
                match = re.search(r"\b(critical|major|minor|warning)\b", line, re.IGNORECASE)
                if match:
                    severity = match.group(1).upper()
                    severity_count[severity] = severity_count.get(severity, 0) + 1

                    if severity == "CRITICAL":
                        critical_alarms.append(line)

            total_alarms = sum(severity_count.values())
            critical_count = severity_count.get("CRITICAL", 0)

            # Store PRE alarm snapshot
            if mode == "pre":
                self.pre_alarm_summary = {
                    "total": total_alarms,
                    "critical": critical_count,
                    "severity": severity_count
                }

            # ---------------- UI MESSAGE ----------------
            summary_lines = [
                f"Total alarms observed : {total_alarms}"
            ]

            for sev, count in sorted(severity_count.items()):
                summary_lines.append(f"{sev} alarms        : {count}")

            summary_text = "\n".join(summary_lines)

            # ---------------- VALIDATION ----------------
            if critical_count > 0:
                return (
                    False,
                    f"{phase_label} failed.\n"
                    f"{summary_text}\n\n"
                    f"Critical alarm details:\n"
                    + "\n".join(critical_alarms)
                )

            # ---------------- POSTCHECK COMPARISON ----------------
            if mode == "post" and hasattr(self, "pre_alarm_summary"):
                pre_critical = self.pre_alarm_summary.get("critical", 0)

                if critical_count > pre_critical:
                    return (
                        False,
                        f"{phase_label} failed.\n"
                        f"Increase in critical alarms detected.\n\n"
                        f"Pre-activity critical alarms : {pre_critical}\n"
                        f"Post-activity critical alarms: {critical_count}\n\n"
                        f"{summary_text}"
                    )

            # ---------------- SUCCESS ----------------
            return (
                True,
                f"{phase_label} completed.\n"
                f"{summary_text}"
            )

        except Exception as e:
            return False, f"Alarm verification failed due to error: {e}"



    ###################################### COMMAND 5TH ###################################
   


    def check_kpi(self):
        
        try:
            mode = self.site_info.get("mode", "pre").lower()
            cmd = "pdc_kpi.pl -i 1 -n 24"

            raw_output = self.sshobj.exec_retry(cmd, wait=30, EOL="#")

            cleaned_output = "\n".join(
                line for line in raw_output.splitlines()
                if line.strip() and not line.strip().startswith("===")
            )

            self.write_log(
                self.phase_name,
                "check_kpi",
                f"Command: {cmd}\n\nOutput:\n{cleaned_output}"
            )

            # -------- PARSE KPI --------
            attach = None
            pdp = None

            for line in cleaned_output.splitlines():
                match = re.match(
                    r"^\d+\s+\d{2}:\d{2}\s+([\d.]+)%\s+([\d.]+)%",
                    line.strip()
                )
                if match:
                    attach = float(match.group(1))
                    pdp = float(match.group(2))
                    break

            if attach is None or pdp is None:
                return False, "KPI values not found in node output"

            # -------- PRE SNAPSHOT --------
            if mode == "pre":
                self.site_info["pre_kpi"] = {
                    "attach": attach,
                    "pdp": pdp
                }

                if attach >= 5 or pdp >= 5:
                    return False, (
                        "KPI threshold breach detected before activity.\n"
                        f"Attach Success Rate : {attach}%\n"
                        f"PDP Activation Rate : {pdp}%\n"
                        "Threshold           : < 5%"
                    )

                return True, (
                    "KPI snapshot captured before activity.\n"
                    f"Attach Success Rate : {attach}%\n"
                    f"PDP Activation Rate : {pdp}%"
                )

            # -------- POST VALIDATION --------
            pre = self.site_info.get("pre_kpi")

            if not pre:
                return False, "PRE KPI data not available for comparison"

            degradation = attach > pre["attach"] or pdp > pre["pdp"]

            if degradation or attach >= 5 or pdp >= 5:
                return False, (
                    "KPI degradation observed after activity.\n\n"
                    "Pre KPI:\n"
                    f"Attach Success Rate : {pre['attach']}%\n"
                    f"PDP Activation Rate : {pre['pdp']}%\n\n"
                    "Post KPI:\n"
                    f"Attach Success Rate : {attach}%\n"
                    f"PDP Activation Rate : {pdp}%"
                )

            return True, (
                "KPI validation completed.\n"
                "No degradation observed.\n"
                f"Attach Success Rate : {attach}%\n"
                f"PDP Activation Rate : {pdp}%"
            )

        except Exception as e:
            return False, f"KPI check failed due to error: {e}"


    ####################################### COMMAND 6TH ##############################################
    
   
    def pre_check_List(self, mode = "pre"):

        try:
            cmd = "listSCs"
            output = self.sshobj.exec_cmd(cmd, wait=30, EOL="#")
            
            self.write_log(self.phase_name, "checkpoint_list", output)

            # ---------------- Validate Output ----------------
            if not output:
                return False, f"{mode.upper()}CHECK FAIL — No output received from listSCs command"

            # Count number of checkpoints
            checkpoint_count = len(
                re.findall(r"CheckpointCompleted|CheckpointActive", output)
            )

            if checkpoint_count == 0:
                return False, (
                    f"{mode.upper()}CHECK FAIL — No checkpoints found in listSCs output"
                )

            # ---------------- PRECHECK ----------------
            if mode.lower() == "pre":
                return True, (
                    f" Checkpoints are present.\n"
                    f"Total checkpoints found: {checkpoint_count}"
                )

            # ---------------- POSTCHECK ----------------
            if mode.lower() == "post":
                return True, (
                    f" Checkpoints are present.\n"
                    f"Total checkpoints found: {checkpoint_count}"
                )

            return False, "ERROR: Invalid mode. Use 'pre' or 'post'."

        except Exception as e:
            self.write_log(self.phase_name, "checkpoint_list", f"Error: {e}")
            return False, f"{mode.upper()}CHECK ERROR — {e}"

    ### COMMAND 7TH ####

    def creation_before_starting_activity(self, mode="pre"):
  
        mode = (mode or "pre").lower()
        prefix = "Pre" if mode == "pre" else "Post"
        checkpoint_name = "INIT"

        try:
            site_info = self.site_info or {}
            activity = site_info.get("activity_name", "activity")
            node = site_info.get("node_name", getattr(self, "node", "NODE"))
            date_str = datetime.now().strftime("%d%m%Y")

            base_checkpoint = f"{prefix}{activity}{date_str}"
            checkpoint_name = base_checkpoint

            # ✅ Ensure shell ready
            for _ in range(5):
                shell_out = self.sshobj.exec_cmd("\n", wait=5, EOL="#")
                if "#" in shell_out:
                    break
                time.sleep(1)

            max_retry = 5
            retry = 0

            while retry < max_retry:
                cmd = f"gsh checkpoint {{ -cpn {checkpoint_name} }} -default_sc true"
                output = self.sshobj.exec_cmd(cmd, wait=180, EOL="#")

                full_output = f"{cmd}\n{output}"

                self.write_log(
                    self.phase_name,
                    "create_checkpoint",
                    full_output
                )

                if not output:
                    return False, full_output

                out = output.lower()

                # ✅ SUCCESS
                if (
                    "checkpointcompleted" in out
                    or "checkpoint created" in out
                    or "#" in output
                ):
                    self.site_info[f"{mode}_created_checkpoint"] = checkpoint_name
                    return True, full_output

                # ✅ SC already exists → show on UI + retry
                if "sc already exists" in out:
                    retry += 1
                    if retry >= max_retry:
                        return False, full_output
                    checkpoint_name = f"{base_checkpoint}_{retry}"
                    continue

                # ❌ REAL FAILURE → show CLI output
                if "error" in out or "failed" in out:
                    return False, full_output

                return False, full_output

            return False, f"Max retries reached\n{full_output}"

        except Exception as e:
            err = f"Exception occurred\nCheckpoint: {checkpoint_name}\nError: {e}"
            self.write_log(self.phase_name, "create_checkpoint_exception", err)
            return False, err



    ### COMMAND 8TH ####
       
    def post_check_List(self, mode="post"):

        try:
            # ---------------- Get PRECHECK count ----------------
            pre_count = self.site_info.get("pre_checkpoint_count")

            if pre_count is None:
                return False, "POSTCHECK FAIL — PRECHECK checkpoint count not available"

            # ---------------- Ensure shell ready ----------------
            self.sshobj.exec_cmd("\n", wait=5, EOL="#")

            # ---------------- Run command ----------------
            cmd = "listSCs"
            raw_output = self.sshobj.exec_retry(cmd, wait=60, EOL="#")

            # ---------------- Clean SSH noise ----------------
            lines = []
            for line in raw_output.splitlines():
                line = line.strip()
                if not line:
                    continue
                # skip banner / prompt
                if line.startswith("Wind River Linux"):
                    continue
                if line.startswith("Installed:"):
                    continue
                if line.startswith("Last login:"):
                    continue
                if line.endswith("#"):
                    continue
                lines.append(line)

            cleaned_output = "\n".join(lines)

            self.write_log(
                self.phase_name,
                "checkpoint_list",
                f"CMD:\n{cmd}\n\nOUT:\n{cleaned_output}"
            )

            # ---------------- Validate actual output ----------------
            if not cleaned_output:
                return False, "POSTCHECK FAIL — listSCs did not return checkpoint data"

            # ---------------- Count checkpoints ----------------
            post_count = len(
                re.findall(r"CheckpointCompleted|CheckpointActive", cleaned_output)
            )

            # ---------------- Compare ----------------
            if post_count > pre_count:
                return True, (
                    f"POSTCHECK OK — New checkpoint created\n"
                    f"PRECHECK count: {pre_count}, POSTCHECK count: {post_count}"
                )

            return False, (
                f"POSTCHECK FAIL — No new checkpoint created\n"
                f"PRECHECK count: {pre_count}, POSTCHECK count: {post_count}"
            )

        except Exception as e:
            self.write_log(self.phase_name, "checkpoint_list", f"Error: {e}")
            return False, f"POSTCHECK ERROR — {e}"
