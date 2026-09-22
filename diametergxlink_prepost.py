import os
import time
import logging
import re
import json
import traceback
import inspect
import time

# Poonam Patil
logger = logging.getLogger(__name__)

class PMPPP1:
    def __init__(self, sshobj, logpath, site_info, batch_id, phase_name, oss_ip):
        self.site_info = site_info
        self.sshobj = sshobj
        self.logpath = logpath
        self.batch_id = batch_id
        self.phase_name = phase_name
        self.node = self.site_info['node_name']
        self.Remark = self.site_info['remark']

    # -------------------- Logging -------------------- #
    def write_log(self, phase_name, Check, output, method="a"):
        sitpath = f"{self.logpath}/{self.batch_id}/{phase_name}/{self.site_info['circle']}/{self.node}"
        try:
            os.makedirs(sitpath, exist_ok=True)
        except Exception:
            pass
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

    def _exec_cmd(self,command,max_timeout=300,wait=5.0,prompt_patterns=None,):
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

    # Checkpoint 1.
    def check_operation_mobile_gateway_status(self):
        try:
            self.sshobj.exec_cmd("environment no more", wait=10, EOL="#")
            cmd = "show mobile-gateway system"
            output = self.sshobj.exec_cmd(cmd,wait=10,EOL="#")
            self.write_log(self.phase_name,"check_operation_mobile_gateway_status",output)
            failed_groups = []
            total_groups = 0
            group_id = ""
            for line in output.splitlines():
                line = line.strip()
                if line.startswith("Group"):
                    parts = line.split(":", 1)
                    if len(parts) > 1:
                        group_id = parts[1].strip()
                elif "Admin State" in line and "Oper" in line :
                    try:
                        admin_state = (line.split("Admin State", 1)[1].split("Oper", 1)[0].replace(":", "").strip())
                        oper_state = (line.split("Oper", 1)[1].replace("state", "").replace("State", "").replace(":", "").strip())
                        total_groups += 1
                        if admin_state != "Up" or oper_state != "Up" :
                            failed_groups.append(f"Group : {group_id}, " f"Admin State : {admin_state}, " f"Oper State : {oper_state}")
                    except Exception as parse_error:
                        self.write_log(self.phase_name,"check_operation_mobile_gateway_status",f"Parse Error : {parse_error}")
            if failed_groups:
                self.site_info['status'] = False
                self.site_info['output'] = "Below groups are not in Up state :\n" + "\n".join(failed_groups)
            else:
                self.site_info['status'] = True
                self.site_info['output'] = f"All groups are in Admin/Oper Up state. Total groups checked : {total_groups}"
            return self.site_info['status'], self.site_info['output']
        except Exception as e:
            self.write_log(self.phase_name,"check_operation_mobile_gateway_status",f"Error: {e}")
            traceback.print_exc()
            return False, f"Error occurred while checking operation status: {e}"

    # Checkpoint 2.
    def check_peer_status(self):
        try:
            application_type = self.site_info.get("application_type")
            checkpoint = f"check_{application_type}_peer_status"     

            self.sshobj.exec_cmd("environment no more", wait=10, EOL="#")
            cmd = f"show mobile-gateway pdn ref-point-peers {application_type}"
            output = self.sshobj.exec_cmd(cmd,wait=10, EOL="#")
            self.write_log(self.phase_name, checkpoint, output)                   
            failed_peers = []
            success_count = 0
            peer_ip = ""
            vm_id = ""
            path_state = ""
            detail_state = ""
            for line in output.splitlines():
                line = line.strip()
                if line.startswith("Peer address"):
                    peer_ip = (line.split(":")[1].strip())
                elif line.startswith("VM"):
                    vm_id = (line.split(":")[1].strip())
                elif "Path Mgmt State" in line:
                    path_state = (line.split("Path Mgmt State :")[1].split("Detail State")[0].strip())
                    detail_state = (line.split("Detail State  :")[1].strip())
                    if path_state == "Active" and detail_state == "Open" :
                        success_count += 1
                    else:
                        failed_peers.append(f"Peer IP : {peer_ip}, " f"VM : {vm_id}")
            if failed_peers:
                self.site_info['status'] = False
                self.site_info['output'] = (
                    f"Below {application_type} peers are not in "
                    f"Active/Open state :\n"
                    + "\n".join(failed_peers)
                )
            else:
                self.site_info['status'] = True
                self.site_info['output'] = f"All {application_type} peers are in Active/Open state. Total validated peers : {success_count}"
            return self.site_info['status'],self.site_info['output']
    
        except Exception as e:
            self.write_log(self.phase_name, checkpoint, f"Error: {e}")
            traceback.print_exc()
            return False, f"Error occurred while checking {application_type} peer status: {e}"

    # Checkpoint 3.
    def check_peer_statistics_stats(self):
        try:
            type_check = self.site_info.get("application_type")
            checkpoint = f"check_{type_check}_peer_status"     
            
            self.sshobj.exec_cmd("environment no more", wait=10, EOL="#")
            cmd = f"show mobile-gateway pdn ref-point-stats {type_check}"
            output = self.sshobj.exec_cmd(cmd, wait=30, EOL="#")
            self.write_log(self.phase_name, checkpoint, output)                    
            peer_statistics = []
            peer_ip = ""
            active_sessions = "0"
            for line in output.splitlines():
                line = line.strip()
                if line.startswith("Peer address"):
                    peer_ip = (line.split(":")[1].strip())
                elif line.startswith("Active Sessions"):
                    active_sessions = (line.split(":")[1].strip())
                    peer_statistics.append(f"Peer IP : {peer_ip}, Active Sessions :  {active_sessions}")
            if len(peer_statistics) == 0:
                self.site_info['status'] = False
                self.site_info['output'] = f"No {type_check} peer statistics found"
            else:
                self.site_info['status'] = True
                self.site_info['output'] = (
                    f"Successfully fetched "
                    f"{type_check} peer statistics for "
                    f"{len(peer_statistics)} peers\n"
                    + "\n".join(peer_statistics)
                )

            return self.site_info['status'],self.site_info['output']
        except Exception as e:
            self.write_log(self.phase_name, checkpoint, f"Error: {e}")
            traceback.print_exc()
            return False, f"Error occurred while fetching {type_check} peer statistics: {e}"

    # Checkpoint 4.
    def check_aggregate_peer_statistics(self):
        try:
            type_check = self.site_info.get("application_type")
            checkpoint = f"check_{type_check}_aggregate_peer_statistics"     

            self.sshobj.exec_cmd("environment no more", wait=10, EOL="#")
            cmd = f"show mobile-gateway pdn ref-point-stats {type_check} aggregate"
            output = self.sshobj.exec_cmd(cmd,wait=30, EOL="#")
            self.write_log(self.phase_name, checkpoint, output)
            active_sessions = "0"
            peer_instances = "0"
            for line in output.splitlines():
                line = line.strip()
                if line.startswith("Active Sessions"):
                    active_sessions = (line.split(":")[1].strip())
                elif line.startswith("Number of peer instances"):
                    peer_instances = (line.split(":")[1].strip())
            self.site_info['status'] = True
            self.site_info['output'] = f"{type_check} aggregate peer statistics fetched successfully. Active Sessions : {active_sessions}, Number of Peer Instances : {peer_instances}"
            return self.site_info['status'],self.site_info['output']
        except Exception as e:
            self.write_log(self.phase_name, checkpoint, f"Error: {e}")
            traceback.print_exc()
            return False,f"Error occurred while fetching {type_check} aggregate peer statistics: {e}"

    # Checkpoint 5.
    def check_aggregate_failure_codes(self):
        try:
            type_check = self.site_info.get("application_type")
            checkpoint = f"check_{type_check}_aggregate_failure_codes"     

            self.sshobj.exec_cmd("environment no more", wait=10, EOL="#")
            cmd = f"show mobile-gateway pdn ref-point-stats {type_check} aggregate failure-codes"
            output = self.sshobj.exec_cmd(cmd, wait=10, EOL="#" )
            self.write_log(self.phase_name, checkpoint, output )

            high_failure_codes = []
            for line in output.splitlines():
                line = line.strip()
                if ":" in line:
                    parts = line.split(":")
                    if len(parts) >= 2:
                        try:
                            value = (parts[1].strip().split()[0])
                            failure_count = int(value)
                            if failure_count > 1000:
                                failure_name = (parts[0].strip())
                                high_failure_codes.append(f"{failure_name} : {failure_count}")
                        except ValueError:
                            continue
            if high_failure_codes:
                self.site_info['status'] = False
                self.site_info['output'] = (
                    f"High {type_check} failure codes detected :\n"
                    + "\n".join(high_failure_codes)
                )
            else:
                self.site_info['status'] = True
                self.site_info['output'] = f"{type_check} aggregate failure codes are within threshold"
            return self.site_info['status'], self.site_info['output']
        except Exception as e:
            error_msg = f"Error occurred while checking {type_check} aggregate failure codes: {e}"
            self.write_log(self.phase_name, checkpoint, error_msg)
            traceback.print_exc()
            return False, error_msg

    # Checkpoint 6.
    def check_system_alarms(self):
        try:
            cmd = "show system alarms"
            output = self.sshobj.exec_cmd(cmd, wait=10, EOL="#")
            self.write_log(self.phase_name, "check_system_alarms", output)
            alarm_summary = ""
            for line in output.splitlines():
                line = line.strip()
                if line.startswith("Alarms"):
                    alarm_summary = line
                    break
            if "Total:0" in alarm_summary:
                self.site_info['status'] = True
                self.site_info['output'] = "No alarms found"
            else:
                self.site_info['status'] = False
                self.site_info['output'] = f"Active alarms found : {alarm_summary}"
            return self.site_info['status'], self.site_info['output']
        except Exception as e:
            self.write_log(self.phase_name, "check_system_alarms", f"Error: {e}")
            traceback.print_exc()
            return False, f"Error occurred while checking system alarms: {e}"

    # Checkpoint 7.
    def check_refpoint_alarms(self):
        try:
            full_output = ""
            lst_data = [98,99,100]
            for i in lst_data:
                output = self.sshobj.exec_cmd(f"show log log-id {i}",wait=30, EOL="#")
                full_output += output + "\n"
            self.write_log(self.phase_name, "check_refpoint_alarms", full_output)        
            critical_alarms = []
            for line in output.splitlines():
                line = line.strip()
                if (("GX" in line.upper() or "GY" in line.upper() or "S6B" in line.upper()) and ("CRITICAL" in line.upper() or "MAJOR" in line.upper())):
                    critical_alarms.append(line)
            if critical_alarms:
                self.site_info['status'] = False
                self.site_info['output'] = f"Alarms found. Total alarms : {len(critical_alarms)}. "
            else:
                self.site_info['status'] = True
                self.site_info['output'] = "No critical alarms found for GX/GY/S6B"
            return (self.site_info['status'],self.site_info['output'])
        except Exception as e:
            self.write_log(self.phase_name, "check_refpoint_alarms", f"Error: {e}")
            traceback.print_exc()
            return False, f"Error occurred while checking GX/GY/S6B alarms: {e}"

    # Checkpoint 8.
    def check_traffic_status(self):
        try:        
            cmd = "show mobile-gateway pdn statistics summary"
            self.sshobj.exec_cmd("environment no more",wait=10,EOL="#")
            output = self.sshobj.exec_cmd(cmd,wait=20,EOL="#")
            self.write_log(self.phase_name,"check_traffic_status",output)

            pdn_sessions = "N/A"
            bearers = "N/A"
            total_ues = "N/A"
            idle_ues = "N/A"
            paging_in_progress = "N/A"

            for line in output.splitlines():
                line = line.strip()
                try:
                    if "PDN Sessions" in line:
                        pdn_sessions = line.split(":")[1].strip().split()[0]
                    elif "Bearers" in line:
                        bearers = line.split(":")[1].strip().split()[0]
                    elif "Total Number of UEs" in line:
                        total_ues = line.split(":")[1].strip().split()[0]
                    elif "Total Number of Idle UEs" in line:
                        idle_ues = line.split(":")[1].strip().split()[0]
                    elif "Paging in progress" in line:
                        paging_in_progress = line.split(":")[1].strip().split()[0]
                except Exception:
                    pass
            self.site_info['status'] = True
            self.site_info['output'] = f"Traffic statistics fetched successfully. "
            return self.site_info['status'], self.site_info['output']
        except Exception as e:
            self.write_log(self.phase_name,"check_traffic_status",f"Error: {e}")
            traceback.print_exc()
            return False, f"Error occurred while fetching traffic statistics: {e}"

    # Checkpoint 9.
    def check_peer_ip_definition(self):
        
        try:
            if self.site_info.get("remark").lower() == "delete" :
                return (True, "This Action will run for Creation and Modification only")

            peer_ip = self.site_info.get("peer_ip")
            print(410, peer_ip, type(peer_ip))
            peer_ip_list = peer_ip.split(",")
            existing_ips = []
            for ip in peer_ip_list:
                ip = ip.strip()
                cmd = f"admin display-config | match context all {ip}"

                output = self.sshobj.exec_retry(cmd,wait=60,EOL="#")
                self.write_log(self.phase_name,"check_peer_ip_definition",output)
                if output and ip in output:
                    existing_ips.append(ip)

            if existing_ips:
                self.site_info['status'] = False
                self.site_info['output'] = (
                    f"Peer IP already exists in configuration: "
                    f"{', '.join(existing_ips)}"
                )

            else:
                self.site_info['status'] = True
                self.site_info['output'] = "Peer IP not present in configuration"

            return self.site_info['status'], self.site_info['output']
        except Exception as e:
            self.write_log(self.phase_name,"check_peer_ip_definition",f"Error: {e}")
            traceback.print_exc()
            return False, f"Error occurred while checking Peer IP definition: {e}"


    # Checkpoint 10.
    def check_ping_connectivity(self):
        try:
            if self.site_info.get("remark").lower() == "delete" :
                return (True, "This Action will run for Creation and Modification only")
            
            peer_ip = self.site_info['peer_ip']

            peer_ip_list = peer_ip.split(",")

            self.sshobj.exec_retry("environment no more", wait=20, EOL="#" )
            service_output = self.sshobj.exec_retry("show service service-using", wait=10, EOL="#" )
            self.write_log(self.phase_name, "check_ping_connectivity", service_output )
            router_id = None

            for line in service_output.splitlines():
                line = line.strip()

                if "CP-SIGNAL" in line.upper():
                    columns = line.split()

                    if len(columns) >= 6:
                        router_id = columns[0]
                        break

            if not router_id:
                return False, "CP-SIGNAL Service ID not found"

            router_output = self.sshobj.exec_retry(f"show router {router_id} interface", wait=60, EOL="#" )
            self.write_log(self.phase_name, "check_ping_connectivity", router_output )

            loopback_ip = None
            lines = router_output.splitlines()

            for index, line in enumerate(lines):
                current_line = line.strip().upper()

                if "GX-LOOPBACK" in current_line or "GY-LOOPBACK" in current_line or "S6B-LOOPBACK" in current_line :

                    for next_index in range(index + 1, min(index + 5, len(lines))):
                        next_line = lines[next_index].strip()

                        if "." in next_line:
                            loopback_ip = (next_line.split()[0].split("/")[0].strip())
                            break

                    break

            if not loopback_ip:
                return False, "Loopback IP not found"

            self.site_info["loopback_ip"] = loopback_ip

            reachable_ips = []
            unreachable_ips = []

            for ip in peer_ip_list:

                ip = ip.strip()

                ping_cmd = f"ping router {router_id} {ip} source {loopback_ip}"

                self.write_log(self.phase_name, "check_ping_connectivity", f"Executing Command : {ping_cmd}" )

                ping_output = self.sshobj.exec_retry(ping_cmd, wait=120, EOL="#" )

                self.write_log(self.phase_name, "check_ping_connectivity", ping_output )

                if "0.00% packet loss" in ping_output or "0% packet loss" in ping_output :
                    reachable_ips.append(ip)

                else:
                    unreachable_ips.append(ip)

            if unreachable_ips:

                self.site_info["status"] = False
                self.site_info["output"] = (
                    f"Ping Not Reachable for : "
                    f"{', '.join(unreachable_ips)}"
                )

            else:

                self.site_info["status"] = True
                self.site_info["output"] = (
                    f"Ping Reachable for : "
                    f"{', '.join(reachable_ips)}"
                )

            return self.site_info["status"], self.site_info["output"]

        except Exception as e:
            self.write_log(self.phase_name, "check_ping_connectivity", f"Error : {e}" )
            traceback.print_exc()
            return False, f"Error occurred while checking  ping status : {e}"
    
    # Checkpoint 11.
    def check_timestamp(self):
        try:
            self.sshobj.exec_cmd("environment no more", wait=10, EOL="#" )
            cmd = "environment time-stamp"
            output = self.sshobj.exec_cmd(cmd, wait=30, EOL="#" )
            self.write_log(self.phase_name, "check_timestamp", output )

            if not output :
                self.site_info['status'] = False
                self.site_info['output'] = "No timestamp output found"

                return self.site_info['status'], self.site_info['output']

            timestamp = ""
            for line in output.splitlines():
                line = line.strip()
                if (
                    line
                    and
                    "environment time-stamp" not in line
                    and
                    not line.startswith("B:")
                    and
                    not line.startswith("*B:")
                    and
                    "#" not in line
                ):
                    timestamp = line
                    break
            if not timestamp:
                self.site_info['status'] = False
                self.site_info['output'] = "Timestamp value not found"
            else:
                self.site_info['status'] = True
                self.site_info['output'] = f"{timestamp}"
            return self.site_info['status'], self.site_info['output']

        except Exception as e:
            self.write_log(self.phase_name, "check_timestamp", f"Error: {e}" )
            traceback.print_exc()
            return False, f"Error occurred while fetching timestamp: {e}"
    # Checkpoint 12.
    def check_vprn_id(self):
        try:
            if self.site_info.get("remark").lower() == "delete" :
                return (True, "This Action will run for Creation and Modification only")

            interface_value = self.site_info.get("interface","")
            application_type = self.site_info.get("application_type")
            print(533,interface_value)
            match = re.search(r'"([^"]*loopback[^"]*)"',interface_value,re.IGNORECASE)
            print(535,match)
            interface_value = match.group(1)
            print(537,interface_value)

            cmd = f"admin display-config | match context all {interface_value}"
            self.sshobj.exec_cmd("environment no more", wait=10, EOL="#" )

            output = self._exec_cmd(cmd)
            self.write_log(self.phase_name,"check_vprn_id", output)
            
            vprn = ""
            for line in output.splitlines():
                line = line.strip()
                if line.startswith("vprn"):
                    vprn = line.split()[1]
                    break
            for line in output.splitlines():
                line = line.strip()
                if line.startswith("vprn"):
                    vprn = line.split()[1]
                    break
            if not vprn :
                self.site_info['status'] = False
                self.site_info['output'] = f"{application_type} VPRN IDs not found"
            else:
                self.site_info['status'] = True
                self.site_info['output'] = f"{application_type} VPRN ID : {vprn}"
            return (self.site_info['status'],self.site_info['output'])
        except Exception as e:
            self.write_log(self.phase_name, "check_vprn_id", f"Error: {e}")
            traceback.print_exc()
            return False, f"Error occurred while fetching VPRN IDs: {e}"
        
    # Checkpoint 13.
    def check_reference_config(self):
        try:
            if self.site_info.get("remark").lower() == "delete" :
                return (True, "This Action will run for Creation and Modification only")

            application_type = self.site_info.get("application_type")
            checkpoint = f"check_{application_type}_reference_config"

            self.sshobj.exec_cmd("environment no more", wait=20, EOL="#" )
            cmd = f'admin display-config | match context all "application-type {application_type}"'
            output = self.sshobj.exec_cmd(cmd, wait=60, EOL="#" )
            self.write_log(self.phase_name, checkpoint, output )

            if not output or not output.strip():
                self.site_info["status"] = False
                self.site_info["output"] = f"No {application_type} peer found in configuration"
                return self.site_info["status"], self.site_info["output"]

            sample_peer = None

            for line in output.splitlines():
                line = line.strip()
                if 'diameter-peer "' in line:
                    try:
                        sample_peer = line.split('"')[1].strip()
                        break
                    except Exception:
                        continue
            if not sample_peer:
                self.site_info["status"] = False
                self.site_info["output"] = f"Unable to fetch sample {application_type} peer"
                return self.site_info["status"], self.site_info["output"]

            config_cmd = f'configure mobile-gateway profile diameter-peer "{sample_peer}"'
            out = self.sshobj.exec_cmd(config_cmd, wait=30, EOL="#" )
            self.write_log(self.phase_name, checkpoint, out )

            info_output = self.sshobj.exec_cmd("info", wait=60, EOL="#")
            self.write_log(self.phase_name, checkpoint, info_output )
            exit_output = self.sshobj.exec_cmd("exit all", wait=20, EOL="#" )
            self.write_log(self.phase_name, checkpoint, exit_output )


            if not info_output or not info_output.strip():
                self.site_info["status"] = False
                self.site_info["output"] = "Reference peer configuration not found"

                return self.site_info["status"], self.site_info["output"]

            application_type = ""
            destination_realm = ""
            diameter_profile = ""
            interface_name = ""
            peer_ip = ""
            origin_host = ""

            for line in info_output.splitlines():
                line = line.strip()
                if line.startswith("application-type"):
                    parts = line.split()
                    if len(parts) > 1:
                        application_type = parts[1]
                elif line.startswith("destination-realm"):
                    if '"' in line:
                        destination_realm = line.split('"')[1]
                elif line.startswith("diameter-profile"):
                    if '"' in line:
                        diameter_profile = line.split('"')[1]
                elif line.startswith("interface router"):
                    values = line.split('"')
                    if len(values) >= 4:
                        interface_name = values[3]
                elif line.startswith("peer "):
                    parts = line.split()
                    if len(parts) >= 2:
                        peer_ip = parts[1]
                elif line.startswith("supported-host"):
                    values = line.split('"')
                    if len(values) >= 2:
                        origin_host = values[1]

            if not diameter_profile:
                self.site_info["status"] = False
                self.site_info["output"] = "Diameter profile not found in reference peer"
                return self.site_info["status"], self.site_info["output"]

            self.site_info["status"] = True
            self.site_info["output"] = (
                f"Reference {application_type} peer configuration fetched successfully. "
                f"Sample Peer={sample_peer}, "
                f"Diameter Profile={diameter_profile}, "
                f"Peer IP={peer_ip}"
            )
            return self.site_info["status"], self.site_info["output"]
        except Exception as e:
            self.write_log(self.phase_name, checkpoint, f"Error : {str(e)}" )
            traceback.print_exc()
            self.site_info["status"] = False
            self.site_info["output"] = f"Error occurred while fetching {application_type} reference configuration : {e}"
            return self.site_info["status"], self.site_info["output"]

    # Checkpoint 14.
    def check_save_configuration(self):
        cmd = "admin save"
        try:
            output = self.sshobj.exec_cmd(cmd,wait=120,EOL="#")
            self.write_log(self.phase_name,"check_save_configuration",output)
            if  "Completed." in output:
                self.site_info['status'] = True
                self.site_info['output'] = "Configuration saved successfully"
            else:
                self.site_info['status'] = False
                self.site_info['output'] = "Configuration save failed"
            return self.site_info['status'], self.site_info['output']
        except Exception as e:
            self.write_log(self.phase_name,"check_save_configuration",f"Error: {e}")
            traceback.print_exc()
            return False, f"Error occurred while saving configuration: {e}"

    # Checkpoint 15.
    def check_diameter_peer_dependency(self):
        try:
            full_output = ""
            action = self.site_info.get("remark").strip().lower()
            if action and action != "delete" :
                return True, "Action not applicable"

            peer_name = self.site_info.get("diameter_peer_name")
            self.sshobj.exec_retry("environment no more",wait=20,EOL="#")

            cmd = f'admin display-config | match context all "{peer_name}"'
            output = self.sshobj.exec_retry(cmd,wait=60,EOL="#")

            full_output += output

            peer_group = ""
            for line in output.splitlines():
                line = line.strip()
                if 'diameter-peer-group-list "' in line:
                    try:
                        peer_group = line.split('"')[1].strip()
                        break
                    except Exception:
                        pass
            if peer_group:
                search_value = peer_group
                self.site_info["diameter_peer_group"] = peer_group
            else:
                search_value = peer_name

            self.sshobj.exec_retry("environment no more",wait=20,EOL="#")
            cmd = f'admin display-config | match context all "{search_value}"'
            apn_output = self.sshobj.exec_retry(cmd,wait=60,EOL="#")
            full_output += apn_output
            self.write_log(self.phase_name,"check_diameter_peer_dependency",full_output)

            commercial_patterns = [
                "www",
                "internet",
                "ims",
                "iphone",
                "fing",
                "testwww",
                "IR-"
            ]

            apn_list = []

            for line in apn_output.splitlines():
                line = line.strip()
                if line.startswith('apn "'):
                    try:
                        apn_name = line.split('"')[1].strip()

                        if any(pattern.lower() in apn_name.lower() for pattern in commercial_patterns):
                            if apn_name not in apn_list:
                                apn_list.append(apn_name)
                    except Exception:
                        pass

            if not apn_list:
                self.write_log(self.phase_name,"check_diameter_peer_dependency",full_output)
                # return True, f"No Commercial APN mapped with {search_value}"
                return True, f"Ready for activity"
            return False, f"We Need to perform apn Activity first"

        except Exception as e:
            self.write_log(self.phase_name,"check_diameter_peer_dependency",f"{full_output}\n\nError : {str(e)}")
            traceback.print_exc()
            return False, f"Error occurred while checking peer dependency : {e}"

            # failed_apns = []
            # for apn in apn_list:
            #     self.sshobj.exec_retry("environment no more",wait=20,EOL="#")

            #     cmd = f'show mobile-gateway pdn apn "{apn}" statistics'
            #     stats_output = self.sshobj.exec_retry(cmd,wait=120,EOL="#")

            #     full_output += f"{stats_output}\n"

            #     homers = 0
            #     visitors = 0
            #     roamers = 0
            #     real_sessions = 0

            #     for line in stats_output.splitlines():
            #         line = line.strip()
            #         try:
            #             if "Homers" in line and ":" in line:
            #                 homers = int(line.split(":")[1].strip().split()[0])
                        
            #             elif "Visitors" in line and ":" in line:
            #                 visitors = int(line.split(":")[1].strip().split()[0])

            #             elif "Roamers" in line and ":" in line:
            #                 roamers = int(line.split(":")[1].strip().split()[0])

            #             elif "Real APN PDN" in line and ":" in line:
            #                 real_sessions = int(line.split(":")[1].strip().split()[0])

            #         except Exception:
            #             pass

            #     full_output += (
            #         f"\nAPN={apn}, "
            #         f"Homers={homers}, "
            #         f"Visitors={visitors}, "
            #         f"Roamers={roamers}, "
            #         f"Real_APN_PDN={real_sessions}\n"
            #     )

            #     if homers > 0 or visitors > 0 or roamers > 0 or real_sessions > 0 :

            #         failed_apns.append(
            #             f"{apn} "
            #             f"(H={homers}, "
            #             f"V={visitors}, "
            #             f"R={roamers}, "
            #             f"Real={real_sessions})"
            #         )

            # self.write_log(self.phase_name,"check_diameter_peer_dependency",full_output)

            # if failed_apns:
            #     return (
            #         False,
            #         "Subscriber Present:\n"
            #         + "\n".join(
            #             failed_apns
            #         )
            #     )

            # return True, f"Subscriber Not Present. Peer {peer_name} can be deleted."


    