"""Diagnostics for the smart-hybrid synchronization engine (stdlib only).

Run from project root with: python scripts/hybrid_sync_cli.py plan
WARNING: `bind`, `sync`, and `undo` may write files; require interactive consent.
"""
from __future__ import annotations
import argparse
import sys
from dataclasses import asdict
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from portable_ai.hybrid_sync import HybridSync


def preview(plan):
    print('统计:', plan.counts())
    print('计划复制字节数:', sum(i.bytes for i in plan.items if i.action=='copy'))
    for item in plan.items[:100]:
        print(f'  [{item.action}] {item.direction or "(blocked)"}: {item.path}' +
              (f' — {item.reason}' if item.reason else ''))
    if len(plan.items)>100:
        print('  ... ',len(plan.items)-100,' more')
    if plan.warnings:
        print('警告:', '; '.join(plan.warnings))


def confirm(question: str, token='YES') -> bool:
    return input(f'{question} 输入 {token} 才执行: ').strip() == token


def main():
    p=argparse.ArgumentParser(description='Portable AI hybrid workspace diagnostic console')
    sub=p.add_subparsers(dest='command',required=True)
    b=sub.add_parser('bind',help='首次授权主机工作区')
    b.add_argument('--host',type=Path,default=None)
    sub.add_parser('status')
    sub.add_parser('plan')
    sub.add_parser('recover')
    sync=sub.add_parser('sync',help='手工预览并同步')
    sync.add_argument('--deletions',action='store_true',help='再单独审批传播删除')
    sub.add_parser('undo',help='回滚最近一次同步')
    args=p.parse_args()
    engine=HybridSync(ROOT)
    if args.command=='bind':
        host=args.host or engine.default_host_path()
        print('将授权本机工作区:',host)
        if not confirm('确认允许本程序读写此目录？'):
            return 1
        auto=confirm('允许之后自动同步安全的单边变更？')
        print('已绑定：',engine.bind(host,auto_sync=auto))
    elif args.command=='status':
        print('模式: smart_hybrid')
        print('项目ID:',engine.project_id)
        print('USB工作区:',engine.usb)
        print('本地工作区:',engine.host or '(未绑定)')
        print('自动同步:',engine.auto_enabled)
        print('中断事务:', engine.unfinished())
    elif args.command=='plan':
        preview(engine.plan())
    elif args.command=='recover':
        print(engine.recover())
    elif args.command=='sync':
        recovery=engine.recover()
        print('中断事务恢复:', recovery)
        if recovery['unresolved']:
            print('存在无法确认的中断事务；拒绝进一步写入')
            return 2
        plan=engine.plan()
        preview(plan)
        if not plan.auto_safe() and (plan.counts()['conflict'] or plan.counts()['blocked'] or plan.warnings):
            print('存在冲突或被阻止的文件；拒绝执行')
            return 2
        if not confirm('确认执行以上复制？'):
            return 1
        allow=False
        if args.deletions:
            allow=confirm('确认删除另一侧已标记为删除的文件？删除前有恢复快照。',token='DELETE')
        result=engine.apply(plan,allow_deletions=allow)
        print(result)
        return 0 if result['ok'] else 2
    elif args.command=='undo':
        previous=engine.latest_transaction()
        if not previous:
            print('没有可以回滚的事务')
            return 1
        print('最近事务:',previous['id'],[op['path'] for op in previous['ops']])
        if not confirm('恢复此事务写入的目标文件？自动同步将暂停',token='ROLLBACK'):
            return 1
        print(engine.rollback(previous['id']))
    return 0


if __name__=='__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError) as e:
        print('ERROR:',e,file=sys.stderr)
        raise SystemExit(2)
