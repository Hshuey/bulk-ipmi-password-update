# README — passchange.py

## Description
This script allows you to safely and quickly change passwords on multiple IPMI/BMC interfaces in parallel using a CSV file input.

- Async (fast)
- Supports passwords with special characters (`!`, `$`, `&`, etc.)
- Automatically handles timeouts, bad logins, and unreachable devices
- Never crashes on bad input — logs every error safely
- Color-coded output (Green = Success, Red = Failure, Yellow = Retry)
- Writes all results to log files

## Files Created
- `success.log` — IPs where password change was successful
- `failure.log` — IPs where password change failed
- `badlines.log` — Any CSV formatting errors or unexpected problems

## Input File Format (`input.csv`)
The CSV must be formatted exactly like this:

```
ip,user,oldpass,newpass
```

Example:

```
192.168.1.100,ADMIN,OldPass123!,NewPass456!
192.168.1.101,ADMIN,P@ssword,StrongerP@ss!
192.168.1.102,ADMIN,pa$$word3,MyNewP@ssw0rd
```

- No header row needed.
- Each line represents one server to update.

## How to Use

1. Ensure `ipmitool` is installed:

   - Debian/Ubuntu:
     ```
     sudo apt install ipmitool
     ```
   - RHEL/CentOS:
     ```
     sudo yum install ipmitool
     ```

2. Create your `input.csv` with your target IPMI servers.

3. Run the script:

   ```
   python3 passchange.py
   ```

4. Review the logs after completion:
   - `success.log`
   - `failure.log`
   - `badlines.log`

## Settings (Defaults)

- Maximum concurrent connections: 10
- Timeout per server: 15 seconds
- Retries on failure: 1

You can adjust these at the top of the script:
```python
COMMAND_TIMEOUT = 15
MAX_CONCURRENT = 10
RETRIES = 1
```

## Notes

- Special characters in passwords are fully supported.
- Failed rows or servers will not interrupt the script — all results are logged cleanly.
- Color-coded output helps quickly identify success (green), failure (red), and retries (yellow).

---
