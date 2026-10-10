"""本机交互式初始化；不接受命令行密码，也不输出密码。"""
import argparse
import os
import getpass
import sys


def main():
    parser=argparse.ArgumentParser(description='创建首个正式管理员（密码不回显）')
    parser.add_argument('--username')
    args=parser.parse_args()
    if not sys.stdin.isatty():
        print('请在本机交互式终端运行初始化，密码不接受管道输入。')
        raise SystemExit(1)
    os.environ['SHOPAGENT_ENV']='production'
    from shopagent.core.sessions import initialize_admin
    from shopagent.contracts.models import BusinessError
    username=args.username or input('正式管理员账号（推荐owner，不用admin）：').strip()
    password=getpass.getpass('密码（至少12位，含字母和数字，不回显）：')
    again=getpass.getpass('再次输入密码：')
    if password!=again:
        print('两次输入不一致，未创建管理员。')
        raise SystemExit(1)
    try:
        initialize_admin(username,password)
    except BusinessError as error:
        print(error.message)
        raise SystemExit(1) from None
    print('正式管理员已创建。原演示数据未迁移。请使用正式模式启动并进入 /merchant。')


if __name__=='__main__':
    main()
