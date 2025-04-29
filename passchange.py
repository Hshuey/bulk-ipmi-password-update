import asyncio
import csv
import traceback
import sys

COMMAND_TIMEOUT = 15
MAX_CONCURRENT = 10
semaphore = asyncio.Semaphore(MAX_CONCURRENT)
RETRIES = 1  # How many retries on failure

# Color codes
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
RESET = "\033[0m"

# Log files
SUCCESS_LOG = "success.log"
FAILURE_LOG = "failure.log"
BADLINES_LOG = "badlines.log"

async def change_ipmi_password(ip, username, old_admin_password, user_id, new_password, attempt=1):
    command = [
        "ipmitool",
        "-I", "lanplus",
        "-H", ip,
        "-U", username,
        "-P", old_admin_password,
        "user", "set", "password", user_id, new_password
    ]

    async with semaphore:
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

            try:
                stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=COMMAND_TIMEOUT)
            except asyncio.TimeoutError:
                process.kill()
                return False, ip, "Timeout (command took too long)"

            stdout = stdout.decode(errors='ignore').strip()
            stderr = stderr.decode(errors='ignore').strip()

            if process.returncode == 0:
                if "Password" in stdout or "Set User" in stdout:
                    return True, ip, "Password changed successfully"
                else:
                    return False, ip, f"Unexpected success output: {stdout}"
            else:
                if "Unauthorized" in stderr or "password" in stderr.lower():
                    return False, ip, "Authentication failed"
                if "hostname" in stderr.lower() or "could not resolve" in stderr.lower():
                    return False, ip, "Host unreachable or DNS failure"
                if "unable to establish" in stderr.lower():
                    return False, ip, "Connection failed"
                if "Invalid user id" in stderr:
                    return False, ip, "Invalid user ID (wrong user slot?)"

                return False, ip, f"IPMI Error: {stderr}"

        except Exception as e:
            return False, ip, f"Unhandled Exception: {str(e)}"

async def find_user_id(ip, username, password, target_username="user"):
    command = [
        "ipmitool",
        "-I", "lanplus",
        "-H", ip,
        "-U", username,
        "-P", password,
        "-c",
        "user", "list"
    ]

    async with semaphore:
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

            try:
                stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=COMMAND_TIMEOUT)
            except asyncio.TimeoutError:
                process.kill()
                return None, None, "Timeout getting user list"

            stdout = stdout.decode(errors='ignore').strip()
            stderr = stderr.decode(errors='ignore').strip()

            if process.returncode != 0:
                return None, None, f"Error running user list: {stderr}"

            lines = stdout.splitlines()
            if not lines:
                return None, None, "No output from ipmitool"

            user_id = None
            free_ids = []

            # Skip the first line
            for line in lines[1:]:
                parts = line.strip().split(',')
                if len(parts) >= 2:
                    id_num = parts[0].strip()
                    name = parts[1].strip()

                    if name.lower() == target_username.lower():
                        user_id = id_num
                    if not name:
                        free_ids.append(id_num)

            return user_id, free_ids, None

        except Exception as e:
            return None, None, f"Exception getting user list: {str(e)}"

async def create_user(ip, username, password, free_id, new_user_password, target_username="user"):
    # 1. Set the new user's name
    set_name_cmd = [
        "ipmitool", "-I", "lanplus",
        "-H", ip,
        "-U", username,
        "-P", password,
        "user", "set", "name", free_id, target_username
    ]
    # 2. Enable the new user
    enable_cmd = [
        "ipmitool", "-I", "lanplus",
        "-H", ip,
        "-U", username,
        "-P", password,
        "user", "enable", free_id
    ]
    # 3. Set privilege (to operator level)
    priv_cmd = [
        "ipmitool", "-I", "lanplus",
        "-H", ip,
        "-U", username,
        "-P", password,
        "channel", "setaccess", "1", free_id,
        "ipmi=on", "link=on", "privilege=3"
    ]
    # 4. Set the new user's password
    password_cmd = [
        "ipmitool", "-I", "lanplus",
        "-H", ip,
        "-U", username,
        "-P", password,
        "user", "set", "password", free_id, new_user_password
    ]

    async with semaphore:
        try:
            cmds = [set_name_cmd, enable_cmd, priv_cmd, password_cmd]
            for cmd in cmds:
                process = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )

                try:
                    stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=COMMAND_TIMEOUT)
                except asyncio.TimeoutError:
                    process.kill()
                    return False, f"Timeout during user creation step"

                if process.returncode != 0:
                    return False, stderr.decode(errors='ignore').strip()

            return True, f"Created user '{target_username}' in slot {free_id}"

        except Exception as e:
            return False, f"Exception during user creation: {str(e)}"

async def process_row(row, line_num):
    try:
        if len(row) != 5:
            log_badline(line_num, f"Invalid format: {row}")
            return False, f"Line {line_num}: Invalid format", row

        ip, username, old_admin_password, new_admin_password, new_user_password = [x.strip() for x in row]

        if not ip or not username or not old_admin_password or not new_admin_password:
            log_badline(line_num, f"Missing data: {row}")
            return False, f"Line {line_num}: Missing data", row

        if new_user_password:
            user_id, free_ids, error = await find_user_id(ip, username, old_admin_password)
            if user_id:
                success, ip, message = await change_ipmi_password(ip, username, old_admin_password, user_id, new_user_password)
                if not success and RETRIES > 0:
                    print(f"{YELLOW}[!] Retry {ip} user account after failure: {message}{RESET}")
                    success, ip, message = await change_ipmi_password(ip, username, old_admin_password, user_id, new_user_password, attempt=2)

            elif free_ids:
                first_free_id = free_ids[0]
                print(f"{YELLOW}[!] No existing 'user' found on {ip}, creating in slot {first_free_id}{RESET}")
                success, message = await create_user(ip, username, old_admin_password, first_free_id, new_user_password)
                if not success:
                    print(f"{RED}[-] Failed to create 'user' on {ip}: {message}{RESET}")
            else:
                print(f"{RED}[-] No available user slots to create 'user' on {ip}{RESET}")

        success, ip, message = await change_ipmi_password(ip, username, old_admin_password, '2', new_admin_password)
        if not success and RETRIES > 0:
            print(f"{YELLOW}[!] Retry {ip} admin account after failure: {message}{RESET}")
            success, ip, message = await change_ipmi_password(ip, username, old_admin_password, '2', new_admin_password, attempt=2)

        return success, ip, message

    except Exception as e:
        log_badline(line_num, f"Exception processing row: {str(e)}")
        return False, f"Line {line_num}: Exception occurred", str(e)

def log_success(ip, message):
    with open(SUCCESS_LOG, "a") as f:
        f.write(f"{ip}: {message}\n")

def log_failure(ip, message):
    with open(FAILURE_LOG, "a") as f:
        f.write(f"{ip}: {message}\n")

def log_badline(line_num, message):
    with open(BADLINES_LOG, "a") as f:
        f.write(f"Line {line_num}: {message}\n")

async def main():
    input_file = "input.csv"
    tasks = []

    open(SUCCESS_LOG, 'w').close()
    open(FAILURE_LOG, 'w').close()
    open(BADLINES_LOG, 'w').close()

    try:
        with open(input_file, newline='') as csvfile:
            reader = csv.reader(csvfile)
            for line_num, row in enumerate(reader, start=1):
                tasks.append(asyncio.create_task(process_row(row, line_num)))

    except Exception as e:
        print(f"{RED}[FATAL] Failed to read CSV file: {str(e)}{RESET}")
        return

    results = await asyncio.gather(*tasks, return_exceptions=True)

    successes = []
    failures = []

    for result in results:
        try:
            if isinstance(result, Exception):
                failures.append(("General Task Error", str(result)))
                continue

            success, ip, message = result
            if success:
                print(f"{GREEN}[+] Success on {ip}: {message}{RESET}")
                successes.append(ip)
                log_success(ip, message)
            else:
                print(f"{RED}[-] Failure on {ip}: {message}{RESET}")
                failures.append((ip, message))
                log_failure(ip, message)

        except Exception as e:
            failures.append(("Post-Processing Error", str(e)))

    print("\n--- SUMMARY ---")
    print(f"{GREEN}Successful changes: {len(successes)}{RESET}")
    print(f"{RED}Failures: {len(failures)}{RESET}")
    if failures:
        print("\nFailed IPs:")
        for ip, error in failures:
            print(f"{RED}{ip}: {error}{RESET}")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception:
        print(f"{RED}[FATAL] Something very unexpected happened:{RESET}")
        traceback.print_exc(file=sys.stdout)
