#!/usr/bin/env python3
"""Set a new password for the New API root admin (user id 1).

Run as root on the server in an interactive terminal. The password is typed by the
administrator, hashed here with bcrypt and sent to the database over stdin; it is
never printed, logged or passed on a command line.
"""
import getpass
import subprocess
import sys

import bcrypt

USER_ID = 1


def main():
    first = getpass.getpass("新密码（至少 8 位）：")
    if len(first) < 8 or len(first.encode()) > 72:
        sys.exit("密码要 8 到 72 个字符，没有修改。")
    if getpass.getpass("再输一遍：") != first:
        sys.exit("两次输入不一致，没有修改。")
    # Go's bcrypt reads the $2a$ prefix; the algorithm is the same as $2b$.
    hashed = "$2a$" + bcrypt.hashpw(first.encode(), bcrypt.gensalt(10)).decode()[4:]
    done = subprocess.run(
        ["docker", "exec", "-i", "easel-newapi-pg", "psql", "-U", "newapi", "-d", "newapi",
         "-v", "ON_ERROR_STOP=1", "-Atq"],
        input=f"UPDATE users SET password='{hashed}' WHERE id={USER_ID} AND role>=100 RETURNING username;\n",
        capture_output=True, text=True, check=False)
    if done.returncode or not done.stdout.strip():
        sys.exit("没有修改成功。")
    print(f"已修改，用户名是 {done.stdout.strip()}。")


if __name__ == "__main__":
    main()
