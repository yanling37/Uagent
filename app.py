"""Portable AI Agent 0.3 — portable client with host-side hybrid workspace."""
from __future__ import annotations
import json
import difflib
import hashlib
import os
import queue
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, simpledialog, ttk

from portable_ai.agent import Agent
from portable_ai.disk_budget import report as disk_report
from portable_ai.hybrid_sync import HybridSync
from portable_ai.storage import Audit, MemoryStore
from portable_ai.tools import ToolRouter
from portable_ai.workspace import Workspace


def app_root():
    return Path(sys.executable if getattr(sys,'frozen',False) else __file__).resolve().parent


class Application:
    def __init__(self, window:tk.Tk):
        self.window=window
        self.root=app_root()
        (self.root/'workspace').mkdir(parents=True,exist_ok=True)
        self.memory=MemoryStore(self.root)
        self.audit=Audit(self.root)
        self.sync=HybridSync(self.root)
        self.workspace=Workspace(self.sync.host if self.sync.bound else self.root/'workspace',self.root)
        self.router=ToolRouter(self.root,self.workspace,self.memory,self.audit,
            cache_root=self._cache_root())
        self.agent=Agent(self.router)
        self.busy=False
        self.syncing=False
        self._closed=False
        self._cloud_consent=set()
        self.notifications=queue.Queue()
        self._selected_provider='DeepSeek'
        self.provider=tk.StringVar(value=self._selected_provider)
        self.model=tk.StringVar(value='deepseek-flash')
        self.key=tk.StringVar(value=os.environ.get('DEEPSEEK_API_KEY',''))
        self.request=tk.StringVar()
        self.status_text=tk.StringVar(value='就绪 · 智能混合工作区 / 按授权操作')
        self.window.title('Portable AI Agent · 智能混合工作区 v0.3')
        self.window.geometry('1050x780')
        self.window.minsize(850,640)
        self._layout()
        if self.sync.bound:
            self._recover_files()
        self.window.after(100,self._poll)
        self.window.after(700,self._startup_sync)
        self.window.after(300000,self._periodic_sync)
        self.window.protocol('WM_DELETE_WINDOW',self._on_close)

    def _layout(self):
        wrapper=ttk.Frame(self.window,padding=14)
        wrapper.pack(fill='both',expand=True)
        heading=ttk.Label(wrapper,text='Portable AI Agent 0.3 · 智能混合工作区',font=('Segoe UI',17,'bold'))
        heading.pack(anchor='w',pady=(0,12))
        top=ttk.Frame(wrapper)
        top.pack(fill='x',pady=3)
        ttk.Label(top,text='模型服务').pack(side='left')
        self.combo=ttk.Combobox(top,textvariable=self.provider,values=['DeepSeek','OpenAI'],state='readonly',width=12)
        self.combo.pack(side='left',padx=(7,15))
        self.combo.bind('<<ComboboxSelected>>',self._provider_changed)
        ttk.Label(top,text='模型名称').pack(side='left')
        ttk.Entry(top,textvariable=self.model,width=22).pack(side='left',padx=(7,15))
        ttk.Label(top,text='API Key').pack(side='left')
        ttk.Entry(top,textvariable=self.key,show='●',width=27).pack(side='left',fill='x',expand=True,padx=(7,0))
        ttk.Label(wrapper,text='API 密钥仅保存在当前进程内存，不写入U盘。发送的任务和工具读取的内容会到达你选择的云端模型。',foreground='#666').pack(anchor='w',pady=(6,10))
        line=ttk.Frame(wrapper)
        line.pack(fill='x',pady=(0,9))
        ttk.Label(line,text='工作区:').pack(side='left')
        self.work_label=ttk.Label(line,text=str(self.workspace.root))
        self.work_label.pack(side='left',padx=8,fill='x',expand=True)
        ttk.Button(line,text='授权本机工作区',command=self._choose_workspace).pack(side='right')
        actions=ttk.Frame(wrapper)
        actions.pack(fill='x',pady=(0,9))
        ttk.Button(actions,text='同步预览/执行',command=self._sync_manual).pack(side='left',padx=(0,8))
        ttk.Button(actions,text='自动同步开关',command=self._toggle_auto).pack(side='left',padx=(0,8))
        ttk.Button(actions,text='回滚最近同步',command=self._rollback_recent).pack(side='left',padx=(0,8))
        ttk.Button(actions,text='工具链检测',command=self._show_toolchains).pack(side='left',padx=(0,8))
        ttk.Button(actions,text='存储空间',command=self._show_disk_budget).pack(side='left',padx=(0,8))
        extras=ttk.Frame(wrapper)
        extras.pack(fill='x',pady=(0,8))
        ttk.Button(extras,text='本地代理',command=self._configure_proxy).pack(side='left',padx=(0,8))
        ttk.Button(extras,text='插件管理',command=self._plugins).pack(side='left',padx=(0,8))
        ttk.Button(extras,text='查看记忆',command=self._memories).pack(side='left',padx=(0,8))
        ttk.Button(extras,text='编辑记忆',command=self._edit_memory).pack(side='left',padx=(0,8))
        ttk.Button(extras,text='回滚文件写入',command=self._rollback_file).pack(side='left',padx=(0,8))
        ttk.Button(extras,text='查看操作审计',command=self._audit_log).pack(side='left',padx=(0,8))
        ttk.Button(actions,text='清空本次对话',command=self._clear_chat).pack(side='right')
        self.console=scrolledtext.ScrolledText(wrapper,wrap='word',state='disabled',height=18,font=('Microsoft YaHei UI',10))
        self.console.pack(fill='both',expand=True)
        self._print('系统', '默认智能混合模式：U盘保留原始项目与记忆，目标电脑承担编译和缓存。首次使用本机须授权；冲突不自动覆盖，删除单独审批。')
        promptbar=ttk.Frame(wrapper)
        promptbar.pack(fill='x',pady=(10,6))
        self.prompt=tk.Text(promptbar,height=3,wrap='word',font=('Microsoft YaHei UI',10))
        self.prompt.pack(side='left',fill='both',expand=True)
        self.sendbtn=ttk.Button(promptbar,text='发送任务',command=self._send,width=13)
        self.sendbtn.pack(side='right',padx=(10,0))
        ttk.Label(wrapper,textvariable=self.status_text,foreground='#666').pack(anchor='w')

    def _provider_changed(self,event=None):
        if self.busy or self.syncing:
            self.provider.set(self._selected_provider)
            return
        self._selected_provider=self.provider.get()
        if self.provider.get()=='DeepSeek':
            self.model.set('deepseek-flash')
            self.key.set(os.environ.get('DEEPSEEK_API_KEY',''))
        else:
            self.model.set('gpt-5-mini')
            self.key.set(os.environ.get('OPENAI_API_KEY',''))
        self.agent.clear()

    def _cache_root(self):
        return (self.sync.state_home / 'caches' / self.sync.project_id) if self.sync.bound else None

    def _activate_host_workspace(self):
        self.workspace=Workspace(self.sync.host if self.sync.bound else self.root/'workspace',self.root)
        self.router=ToolRouter(self.root,self.workspace,self.memory,self.audit,
                               cache_root=self._cache_root())
        self.agent=Agent(self.router)
        self.work_label.configure(text=str(self.workspace.root))
        self._print('工作区','当前授权目录：'+str(self.workspace.root))
        self._recover_files()

    def _choose_workspace(self):
        if self.busy or self.syncing:
            return
        if self.sync.bound:
            messagebox.showinfo('本机工作区', '此项目已绑定到：\n'+str(self.sync.host)+
                                '\n\n如需迁移到新目录，应先备份并通过后续迁移向导处理；不能直接切换以免丢失同步基线。')
            return
        suggested=self.sync.default_host_path()
        use_default=messagebox.askyesno('首次授权本机目录',
            '推荐建立本机工作区：\n'+str(suggested)+
            '\n\n确认使用此目录？选“否”可手工选择其他目录。\n\n'+
            '本程序只同步工作区内的非敏感项目文件。')
        if use_default:
            chosen=suggested
        else:
            path=filedialog.askdirectory(title='选择要授权的非系统目录')
            if not path:
                return
            chosen=Path(path)
        if not messagebox.askyesno('确认本机工作区访问权限',
              '确认允许本 Agent 在以下目录创建和修改项目文件？\n\n'+str(chosen)+
              '\n\n首次同步会另外显示预览，操作前保留恢复快照。'):
            return
        auto=messagebox.askyesno('自动增量同步授权',
            '是否授权本程序每次启动、完成 AI 任务及程序运行期间定期执行安全增量同步？\n\n'+
            '只自动处理单侧变更。遇到冲突、删除或不安全文件会停止本轮自动同步；每次覆盖前备份。')
        try:
            self.sync.bind(chosen,auto_sync=auto)
            self._activate_host_workspace()
            self._request_sync(manual=True)
        except Exception as exc:
            messagebox.showerror('工作区绑定失败',str(exc))

    def _sync_manual(self):
        self._request_sync(manual=True)

    def _startup_sync(self):
        if self.sync.bound:
            self._print('同步','检测到已授权的本机工作区。'+
                        ('准备自动增量同步。' if self.sync.auto_enabled else '尚未审核首次同步或自动同步已关闭，请手工预览。'))
            if self.sync.auto_enabled:
                self._request_sync(manual=False)

    def _periodic_sync(self):
        if self.sync.bound and self.sync.auto_enabled and not self.busy and not self.syncing:
            self._request_sync(manual=False)
        self.window.after(300000,self._periodic_sync)

    def _request_sync(self,manual=False):
        if not self.sync.bound:
            if manual:
                messagebox.showinfo('尚未绑定','请先点击“授权本机工作区”，首次授权后可自动复制。')
            return
        if self.busy or self.syncing:
            if manual:
                messagebox.showwarning('任务进行中','请在 Agent 任务或同步完成后再执行。')
            return
        self.syncing=True
        self.status_text.set('正在扫描文件指纹并核对同步记录...')
        def worker():
            try:
                recovered=self.sync.recover()
                plan=self.sync.plan()
                if not manual and recovered.get('unresolved'):
                    raise RuntimeError('存在未解决的中断事务，必须人工核对后才能继续自动同步')
                if not manual:
                    if not plan.auto_safe():
                        self.sync.set_auto(False)
                        self.notifications.put(('sync_attention',(plan,recovered)))
                    elif plan.counts()['copy']:
                        outcome=self.sync.apply(plan)
                        self.notifications.put(('sync_done',(outcome,recovered)))
                    else:
                        outcome=self.sync.apply(plan)  # Commit already-identical common versions.
                        self.notifications.put(('sync_done',(outcome,recovered)))
                else:
                    self.notifications.put(('sync_plan',(plan,recovered)))
            except Exception as exc:
                self.notifications.put(('sync_error',str(exc)))
        threading.Thread(target=worker,daemon=True).start()

    def _sync_plan_text(self,plan):
        counts=plan.counts()
        lines=[f'复制 {counts["copy"]} 项；删除待审批 {counts["delete"]} 项；'+
               f'冲突 {counts["conflict"]} 项；不安全 {counts["blocked"]} 项。',
               '复制数据量约 '+f'{sum(i.bytes for i in plan.items if i.action=="copy")/1024**2:.2f} MiB',
               '工作区：'+str(self.workspace.root),'']
        for item in plan.items[:45]:
            lines.append(f'[{item.action}] {item.direction or ""} {item.path} '+item.reason)
        if len(plan.items)>45:
            lines.append(f'...还有 {len(plan.items)-45} 项未展示，执行时仍重新核对全部文件。')
        lines.extend(plan.warnings)
        return '\n'.join(lines)

    def _manual_plan_confirm(self,plan,recovered):
        self.syncing=False
        if recovered.get('unresolved'):
            self._print('异常恢复','仍存在未解决的中断事务：'+json.dumps(recovered,ensure_ascii=False))
            messagebox.showwarning('同步暂停','有未完成事务无法安全恢复，请先检查。')
            return
        detail=self._sync_plan_text(plan)
        self._print('同步预览',detail)
        counts=plan.counts()
        if counts['conflict'] or counts['blocked'] or plan.warnings:
            messagebox.showwarning('同步冲突 / 风险文件',
                detail[:2000]+'\n\n本轮不允许任何文件覆盖，请人工解决后再同步。')
            return
        if not counts['copy'] and not counts['delete']:
            self._run_apply(plan)  # Still persist the matching file fingerprint baseline.
            return
        if not messagebox.askyesno('审批本轮增量同步',
                detail[:2400]+'\n\n只进行受限目录内的复制；删除需要第二次单独确认。是否继续？'):
            return
        delete_confirm=False
        if counts['delete']:
            delete_confirm=messagebox.askyesno('单独确认删除操作',
                f'有 {counts["delete"]} 个文件被判定为单边删除。\n'+
                '批准后会在另一端删除对应文件，删除前保存完整恢复快照。\n\n'+
                '选“否”只复制其他文件，保留待删除项目。是否批准传播删除？')
        self._run_apply(plan,delete_confirm)

    def _run_apply(self,plan,allow_deletions=False):
        if self.busy or self.syncing:
            return
        self.syncing=True
        self.status_text.set('正在同步并校验，每个文件写入前保存快照...')
        def worker():
            try:
                result=self.sync.apply(plan,allow_deletions=allow_deletions)
                self.notifications.put(('sync_done',(result,{'recovered':[],'unresolved':[]})))
            except Exception as exc:
                self.notifications.put(('sync_error',str(exc)))
        threading.Thread(target=worker,daemon=True).start()

    def _toggle_auto(self):
        if not self.sync.bound:
            messagebox.showinfo('尚未绑定','请先授权本机工作区。')
            return
        if self.busy or self.syncing:
            return
        if not self.sync.initial_reviewed:
            messagebox.showinfo('需要先审核', '首次同步预览并确认后才能开启后续自动同步。')
            return
        enabled=not self.sync.auto_enabled
        if enabled and not messagebox.askyesno('开启自动同步',
            '每次启动、AI任务结束及每5分钟将自动处理单边变更，写入前备份。冲突、删除及不安全文件将暂停。是否授权？'):
            return
        try:
            self.sync.set_auto(enabled)
            self._print('同步','自动增量同步已'+('启用' if enabled else '关闭'))
            if enabled:
                self._request_sync(manual=False)
        except Exception as exc:
            messagebox.showerror('错误',str(exc))

    def _rollback_recent(self):
        if self.busy or self.syncing or not self.sync.bound:
            return
        txn=self.sync.latest_transaction()
        if not txn:
            messagebox.showinfo('回滚','没有已记录的可回滚同步事务。')
            return
        paths=[o['path'] for o in txn['ops']]
        if not messagebox.askyesno('回滚同步事务',
             f'事务 {txn["id"]}，涉及 {len(paths)} 个文件：\n'+
             '\n'.join(paths[:20])+ '\n\n'+
             '仅恢复本次同步写入的目标文件；若后续文件又被编辑，则拒绝覆盖。'+
             '回滚后关闭自动同步，须重新人工预览。是否继续？'):
            return
        self.syncing=True
        def worker():
            try:
                outcome=self.sync.rollback(txn['id'])
                self.notifications.put(('sync_rollback',outcome))
            except Exception as exc:
                self.notifications.put(('sync_error',str(exc)))
        threading.Thread(target=worker,daemon=True).start()

    def _on_close(self):
        if self.busy or self.syncing:
            messagebox.showwarning('仍在执行', '当前 AI 任务或文件同步尚未完成。请先结束任务，避免写入中途断开。')
            return
        if self.sync.bound and not messagebox.askyesno('退出 Portable AI Agent',
             '退出前请确认已执行“同步预览/执行”。\n本程序不能替你自动弹出 U 盘。\n\n确认现在退出？'):
            return
        self._closed=True
        self.key.set('')
        self.window.destroy()

    def _print(self,who,message):
        self.console.configure(state='normal')
        self.console.insert('end',f'【{who}】\n{message}\n\n')
        self.console.see('end')
        self.console.configure(state='disabled')

    def _send(self):
        if self.busy or self.syncing:
            return
        if not self.sync.bound:
            messagebox.showwarning('需要授权工作区', '请先点击“授权本机工作区”并完成首次同步审核。未授权时不会发送任务或读取项目文件。')
            return
        if not self.sync.initial_reviewed:
            messagebox.showwarning('首次同步尚未确认',
                '请先点击“同步预览/执行”完成首次授权目录的同步审核，再让 AI 修改本地文件。')
            return
        prompt=self.prompt.get('1.0','end').strip()
        key=self.key.get().strip()
        if not prompt:
            return
        if not key:
            messagebox.showwarning('缺少密钥','请填入本次会话所用 API Key。')
            return
        if not self.model.get().strip():
            messagebox.showwarning('缺少模型', '请输入模型名称。')
            return
        provider=self.provider.get()
        if provider not in self._cloud_consent:
            if not messagebox.askyesno('确认云端数据传输与 API 费用',
                '所选服务：'+provider+'\n\n发送任务后，任务文本、选取的本地记忆、以及工具读取的项目内容可能被发送给该服务。API 使用可能产生费用。请不要提交密钥或无权传输的文件。\n\n是否授权本次会话使用此服务？此确认不代替每次写入或执行的审批。'):
                return
            self._cloud_consent.add(provider)
        self._print('用户',prompt)
        self.prompt.delete('1.0','end')
        self.busy=True
        self.sendbtn.configure(state='disabled')
        self.combo.configure(state='disabled')
        provider=self.provider.get(); model=self.model.get().strip()
        def worker():
            try:
                answer=self.agent.run(prompt,provider,key,model,self._approve,
                    lambda s:self.notifications.put(('status',s)))
                self.notifications.put(('reply',answer))
            except Exception as err:
                self.notifications.put(('error',str(err)))
        threading.Thread(target=worker,daemon=True).start()

    def _approve(self,name,args):
        event=threading.Event()
        result={'approved':False}
        def dialog():
            win=tk.Toplevel(self.window)
            win.title('审批本次工具操作：'+name)
            win.geometry('860x600')
            win.transient(self.window)
            win.grab_set()
            ttk.Label(win,text='以下操作具有写入或执行权限。请审核完整内容后决定。',
                      font=('Segoe UI',12,'bold')).pack(anchor='w',padx=14,pady=(12,8))
            view=scrolledtext.ScrolledText(win,wrap='none',font=('Consolas',10))
            view.pack(fill='both',expand=True,padx=14,pady=7)
            if name=='write_file':
                newcontent=args.get('content','')
                target=args.get('path','')
                try:
                    f=self.workspace.resolve(target)
                    before=f.read_text(encoding='utf-8') if f.is_file() and f.stat().st_size<=500000 else ''
                    diff=''.join(difflib.unified_diff(before.splitlines(keepends=True),
                        newcontent.splitlines(keepends=True),fromfile='old/'+target,tofile='new/'+target))
                except Exception as error:
                    diff='无法生成差异预览: '+str(error)+'\n\n完整新文件内容:\n'+newcontent
                detail=('目标文件: '+str(target)+'\n字节数: '+str(len(newcontent.encode('utf-8')))+
                    '\nSHA256: '+hashlib.sha256(newcontent.encode('utf-8')).hexdigest()+'\n\n'+diff)
            else:
                detail=json.dumps(args,ensure_ascii=False,indent=2)
                if name=='compile_source':
                    detail=('警告：编译工具和构建脚本可能在宿主电脑执行代码，'+
                            '此原型不是安全沙盒。只编译你信任的项目。\n\n'+detail)
            view.insert('1.0',detail)
            view.configure(state='disabled')
            btns=ttk.Frame(win)
            btns.pack(fill='x',padx=14,pady=(6,12))
            def finish(approved=False):
                result['approved']=approved
                try:
                    win.destroy()
                finally:
                    event.set()
            ttk.Button(btns,text='拒绝（默认）',command=lambda:finish(False)).pack(side='right',padx=(8,0))
            ttk.Button(btns,text='批准本次操作',command=lambda:finish(True)).pack(side='right')
            win.protocol('WM_DELETE_WINDOW',lambda:finish(False))
        # Only _poll on the Tk main thread may create or manipulate widgets.
        self.notifications.put(('approval',(dialog,event,result)))
        event.wait()
        return result['approved']

    def _poll(self):
        try:
            while True:
                kind,body=self.notifications.get_nowait()
                if kind=='approval':
                    dialog,event,result=body
                    try:
                        if not self._closed:
                            dialog()
                        else:
                            event.set()
                    except Exception as exc:
                        result['approved']=False
                        event.set()
                        self._print('审批失败',str(exc))
                elif kind=='status':
                    self.status_text.set(body)
                elif kind=='sync_plan':
                    plan,recovered=body
                    self._manual_plan_confirm(plan,recovered)
                    if not self.syncing:
                        self.status_text.set('同步预览已完成')
                elif kind=='sync_attention':
                    self.syncing=False
                    plan,recovered=body
                    msg=self._sync_plan_text(plan)
                    self._print('同步暂停',msg+'\n自动同步已关闭，请人工处理后再启用。')
                    self.status_text.set('同步有冲突或待审批删除，已暂停自动模式')
                    if recovered.get('unresolved'):
                        self._print('恢复问题',json.dumps(recovered,ensure_ascii=False))
                elif kind=='sync_done':
                    self.syncing=False
                    outcome,recovered=body
                    self._print('同步结果',json.dumps(outcome,ensure_ascii=False,indent=2)[:10000])
                    if recovered.get('recovered') or recovered.get('unresolved'):
                        self._print('中断事务恢复',json.dumps(recovered,ensure_ascii=False))
                    self.status_text.set('同步已完成' if outcome.get('ok') else '同步有错误，检查结果后重新预览')
                    if not outcome.get('ok'):
                        self.sync.set_auto(False)
                elif kind=='sync_rollback':
                    self.syncing=False
                    self._print('回滚结果',json.dumps(body,ensure_ascii=False,indent=2))
                    self.status_text.set('回滚完成；自动同步已关闭')
                elif kind=='sync_error':
                    self.syncing=False
                    self._print('同步异常',body)
                    self.status_text.set('同步异常；请检查 U 盘连接并重试')
                    if self.sync.bound:
                        self.sync.set_auto(False)
                else:
                    self._print('AI' if kind=='reply' else '错误',body)
                    self.busy=False
                    self.sendbtn.configure(state='normal')
                    self.combo.configure(state='readonly')
                    self.status_text.set('就绪')
                    if kind=='reply' and self.sync.bound and self.sync.auto_enabled:
                        self.window.after(600,self._request_sync)
        except queue.Empty:
            pass
        except Exception as exc:
            # A removable drive or malformed log must not kill the UI event pump.
            self._print('界面操作异常',str(exc))
        finally:
            if not self._closed:
                self.window.after(100,self._poll)

    def _show_toolchains(self):
        state=self.router.devkit.status()['toolchains']
        lines=[f'{k:11} {v["source"]:9} {v["path"] or "not found"}' for k,v in state.items()]
        self._print('工具链检测','\n'.join(lines))

    def _show_disk_budget(self):
        # Local disk inspection only. No network calls or file writes.
        data=disk_report(self.root)
        gib=lambda n: f'{n/1024**3:.2f} GiB'
        lines=[
            '当前程序所在卷容量: '+gib(data['volume_total']),
            '已用空间: '+gib(data['volume_used']),
            '剩余空间: '+gib(data['volume_free']),
            '', 'Portable AI 文件夹类别占用:'
        ]
        for key, value in data['categories'].items():
            lines.append(f'  {key:12} {gib(value)}')
        if data['partial_counts']:
            lines.append('部分目录文件过多，仅统计前150000项: '+', '.join(data['partial_counts']))
        if data['low_space']:
            lines.append('提醒：剩余空间少于8 GiB。请清理构建缓存，避免继续大量写入。')
        self._print('U盘存储检查','\n'.join(lines))

    def _configure_proxy(self):
        if self.busy or self.syncing:
            messagebox.showinfo("任务进行中", "请等待当前任务或同步完成后再更改设置。")
            return
        old=self.router.proxy_port or 7890
        port=simpledialog.askinteger('本程序代理','输入本机 HTTP 代理端口（如 Clash 7890/7897）；仅本 Agent 生效。',
                                  initialvalue=old,minvalue=1024,maxvalue=65535)
        if port is None:
            return
        if messagebox.askyesno('确认',f'将本 Agent 后续 API 请求发送到 127.0.0.1:{port}？\n不会变更 Windows VPN 配置。'):
            state=self.router.execute('set_local_proxy',{'port':port},lambda n,a:True)
            self._print('代理',json.dumps(state,ensure_ascii=False))

    def _plugins(self):
        if self.busy or self.syncing:
            messagebox.showinfo("任务进行中", "请等待当前任务或同步完成后再更改设置。")
            return
        plugins=self.router.list_plugins()
        if not plugins:
            messagebox.showinfo('插件','插件目录为空。')
            return
        names='\n'.join(f'{p["name"]}: {"已信任" if p["trusted"] else "未信任"}\nSHA256: {p["sha256"]}' for p in plugins)
        name=simpledialog.askstring('插件管理',names+'\n\n输入需要审核并信任的插件目录名（留空则取消）：')
        if not name:
            return
        matches=[p for p in plugins if p['name']==name]
        if not matches:
            messagebox.showwarning('无效','没有此插件。'); return
        plugin=matches[0]
        if messagebox.askyesno('危险：执行本地插件代码',
            f'插件为不受沙盒限制的 Python 代码。务必先检查其源代码。\n\n{name}\nSHA256: {plugin["sha256"]}\n\n确认信任并加载吗？'):
            self.router.trust_plugin(name)
            self.router.load_trusted_plugins()
            self._print('插件',f'已信任 {name}（以后文件发生改动会自动停用）。')

    def _memories(self):
        rows=self.memory.search('',limit=30)
        self._print('最近记忆',json.dumps(rows,ensure_ascii=False,indent=2)[:12000] if rows else '暂无持久记忆。')

    def _recover_files(self):
        records=self.workspace.recover()
        if records:
            self._print('文件写入恢复核对',json.dumps(records,ensure_ascii=False,indent=2))

    def _rollback_file(self):
        if self.busy or self.syncing or not self.sync.bound:
            return
        try:
            receipts=[]
            for path in self.workspace.transactions.glob('*.json'):
                record=json.loads(path.read_text(encoding='utf-8'))
                if record.get('status')=='committed':
                    receipts.append((path.stat().st_mtime_ns,record))
            if not receipts:
                messagebox.showinfo('回滚文件写入','没有可回滚的普通文件写入。')
                return
            records=[r for _,r in sorted(receipts,reverse=True)[:20]]
            self._print('可回滚写入', '\n'.join(r['id']+'  '+r['path'] for r in records))
            transaction=simpledialog.askstring('回滚文件写入','输入控制台中要回滚的事务 ID：',initialvalue=records[0]['id'])
            if not transaction:
                return
            record=next((r for r in records if r['id']==transaction.strip()),None)
            if not record:
                raise ValueError('请选择已展示的事务 ID')
            if not messagebox.askyesno('批准回滚', '目标：'+record['path']+'\n恢复写入前内容；若原先不存在则删除。后续已有更改时拒绝覆盖。是否继续？'):
                self.audit.record('file_rollback','denied')
                return
            self.sync.set_auto(False)
            result=self.workspace.rollback(record['id'])
            self.audit.record('file_rollback','success',record['id'])
            self._print('文件回滚',json.dumps(result,ensure_ascii=False)+'\n自动同步已关闭，请重新审核同步方向。')
        except Exception as exc:
            messagebox.showerror('文件回滚失败',str(exc))

    def _edit_memory(self):
        if self.busy or self.syncing:
            return
        try:
            self._memories()
            memory_id=simpledialog.askinteger('编辑记忆','输入展示的记忆 ID（最多显示30条）：',minvalue=1)
            if memory_id is None:
                return
            row=next((r for r in self.memory.search('',limit=30) if r['id']==memory_id),None)
            if not row:
                raise ValueError('请输入最近展示的记忆 ID')
            content=simpledialog.askstring('编辑记忆','输入新内容（保留历史版本）：',initialvalue=row['content'])
            if content is None:
                return
            if not messagebox.askyesno('批准记忆修改',f'记忆 {memory_id}，版本 {row["version"]}\n\n'+content):
                self.audit.record('memory_update','denied')
                return
            version=self.memory.update(memory_id,content,row['version'])
            self.audit.record('memory_update','success',f'id={memory_id}, version={version}')
            self._print('记忆已更新',f'ID {memory_id}，版本 {version}；原版本已保留。')
        except Exception as exc:
            messagebox.showerror('记忆修改失败',str(exc))

    def _audit_log(self):
        try:
            # Bounded tail read; logs may be large on long-lived portable drives.
            path=self.audit.file
            if not path.exists():
                self._print('操作审计','暂无操作记录。')
                return
            with path.open('rb') as handle:
                size=handle.seek(0,2)
                handle.seek(max(0,size-65536))
                if size>65536:
                    handle.readline()
                lines=handle.read().decode('utf-8',errors='replace').splitlines()[-100:]
            self._print('操作审计（最近最多100条）','\n'.join(lines) or '暂无操作记录。')
        except OSError as exc:
            messagebox.showerror('无法读取审计',str(exc))

    def _clear_chat(self):
        if self.busy or self.syncing:
            messagebox.showinfo("任务进行中", "请等待当前任务或同步完成后再更改设置。")
            return
        self.agent.clear()
        self._print('系统','本次会话上下文已清空；持久记忆没有删除。')


def main():
    win=tk.Tk()
    try:
        Application(win)
    except Exception as exc:
        messagebox.showerror('Portable AI 启动失败', '无法初始化应用。请确认所在目录可写、U盘仍连接，且配置未损坏。\n\n'+str(exc))
        win.destroy()
        return 1
    win.mainloop()
    return 0


if __name__=='__main__':
    raise SystemExit(main())
