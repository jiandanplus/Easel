import argparse
import json
import os
from pathlib import Path
from .config import Settings
from .store import Store
from .api import create_app
from .auth import PlatformError

def main(argv=None):
    parser=argparse.ArgumentParser(prog="easel_platform")
    commands=parser.add_subparsers(dest="command",required=True)
    serve=commands.add_parser("serve")
    serve.add_argument("--database",type=Path,required=True)
    serve.add_argument("--host",default="127.0.0.1")
    serve.add_argument("--port",type=int,default=8790)
    plan=commands.add_parser("plan").add_subparsers(dest="plan_command",required=True).add_parser("create")
    plan.add_argument("--database",type=Path,required=True)
    for flag in ("id","name"): plan.add_argument("--"+flag,required=True)
    for flag in ("tokens","concurrency","days"): plan.add_argument("--"+flag,type=int,required=True)
    plan.add_argument("--model",action="append",required=True)
    grant=commands.add_parser("grant")
    grant.add_argument("--database",type=Path,required=True)
    for flag in ("email","plan","key"): grant.add_argument("--"+flag,required=True)
    args=parser.parse_args(argv)
    try:
        settings=Settings(args.database,upstream_url=os.environ.get("EASEL_PLATFORM_UPSTREAM_URL",""),upstream_key=os.environ.get("EASEL_PLATFORM_UPSTREAM_KEY",""),upstream_model=os.environ.get("EASEL_PLATFORM_UPSTREAM_MODEL",""),public_model=os.environ.get("EASEL_PLATFORM_PUBLIC_MODEL",""))
        if args.command=="serve":
            import uvicorn
            uvicorn.run(create_app(settings),host=args.host,port=args.port)
        else:
            store=Store(settings)
            if args.command=="plan": result=store.create_plan(args.id,args.name,args.tokens,args.concurrency,args.days,args.model)
            else: result=store.grant(args.email,args.plan,args.key)
            print(json.dumps(result))
    except PlatformError as exc:
        parser.exit(1,exc.code+"\n")
    except (ValueError, OSError):
        parser.exit(1,"Invalid platform configuration\n")

if __name__=="__main__": main()
