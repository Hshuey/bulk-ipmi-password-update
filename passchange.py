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

async def change_ipmi_password(ip, username, old_password, new_password, attempt=1):
    command = [
        "ipmitool",
        "-I", "lanplus",
        "-H", ip,
        "-U", username,
        "-P", old_password,
        "user", "set", "password", "2", new_password
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
                # Handle common errors
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

async def process_row(row, line_num):
    try:
        if len(row) != 4:
            log_badline(line_num, f"Invalid format: {row}")
            return False, f"Line {line_num}: Invalid format", row

        ip, username, old_password, new_password = [x.strip() for x in row]

        # Check for empty fields
        if not ip or not username or not old_password or not new_password:
            log_badline(line_num, f"Missing data: {row}")
            return False, f"Line {line_num}: Missing data", row

        success, ip, message = await change_ipmi_password(ip, username, old_password, new_password)

        # If it failed, retry once if allowed
        if not success and RETRIES > 0:
            print(f"{YELLOW}[!] Retry {ip} after failure: {message}{RESET}")
            success, ip, message = await change_ipmi_password(ip, username, old_password, new_password, attempt=2)

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

    # Clear logs
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

    # Summary
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
