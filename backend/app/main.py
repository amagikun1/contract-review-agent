import asyncio
import json
import logging
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import StreamingResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from .config import settings
from .repository import Repository
from .models import ReviewInput, ApprovalInput
from .workflow import build_workflow, markdown_report
from .rag import knowledge

settings.data_dir.mkdir(parents=True,exist_ok=True)
uploads=settings.data_dir/'uploads'; uploads.mkdir(exist_ok=True)
repo=Repository(settings.data_dir/'contracts.sqlite3')

@asynccontextmanager
async def lifespan(app):
    # 仅在服务启动时恢复任务，模块导入及单元测试收集不能修改运行数据。
    for item in repo.list_contracts():
        if item['status']=='reviewing': repo.update_contract(item['id'],status='failed',error='服务重启中断审查，请重新审查。')
    yield

app=FastAPI(title='合同审查工作台',version='0.1.0',lifespan=lifespan)
app.add_middleware(CORSMiddleware,allow_origins=['http://localhost:5173','http://127.0.0.1:5173'],allow_methods=['*'],allow_headers=['*'])
executor=ThreadPoolExecutor(max_workers=2)
lock=threading.RLock()
events={}
active=set()

def get(cid):
    item=repo.get_contract(cid)
    if item is None: raise HTTPException(404,'合同不存在')
    return item

def event(cid,step,detail):
    with lock: events.setdefault(cid,[]).append({'step':step,'detail':detail,'time':datetime.now(timezone.utc).isoformat()})

@app.get('/api/health')
def health():
    return {'status':'ok','llm_configured':bool(settings.dashscope_api_key),'model':settings.qwen_model,'retrieval':'中文 TF-IDF 向量检索','knowledge_count':len(knowledge.documents)}

@app.get('/api/knowledge')
def documents(): return knowledge.documents

@app.get('/api/contracts')
def list_contracts():
    return [{k:v for k,v in c.items() if k not in ['stored_path','text','pages']} for c in repo.list_contracts()]

@app.post('/api/contracts',status_code=201)
async def upload(file: UploadFile=File(...)):
    filename=Path((file.filename or 'contract').replace('\\','/')).name
    ext=Path(filename).suffix.lower()
    if ext not in {'.pdf','.docx','.txt','.md','.png','.jpg','.jpeg'}: raise HTTPException(415,'支持 PDF、DOCX、TXT、MD、PNG、JPG')
    target=uploads/(uuid.uuid4().hex+ext)
    size=0
    try:
        with target.open('wb') as f:
            while chunk:=await file.read(1024*1024):
                size+=len(chunk)
                if size>settings.max_upload_mb*1024*1024: raise HTTPException(413,f'文件不能超过 {settings.max_upload_mb} MB')
                f.write(chunk)
        if not size: raise HTTPException(400,'文件为空')
        result=repo.create_contract(filename,str(target))
        return {k:v for k,v in result.items() if k!='stored_path'}
    except Exception:
        target.unlink(missing_ok=True)
        raise
    finally: await file.close()

@app.get('/api/contracts/{cid}')
def detail(cid: str): return {k:v for k,v in get(cid).items() if k!='stored_path'}

def run_review(cid):
    try:
        result={}
        graph=build_workflow(lambda s,d:event(cid,s,d))
        for update in graph.stream({'path':get(cid)['stored_path'],'messages':[]},config={'recursion_limit':24},stream_mode='updates'):
            for node,values in update.items():
                result.update(values or {})
                if node=='parse': repo.update_contract(cid,text=values['text'],pages=values['pages'])
        repo.update_contract(cid,text=result['text'],pages=result['pages'],analysis=result['analysis'],status='pending_review',error='')
        event(cid,'done','审查完成，请进行人工复核')
    except Exception as exc:
        # 不记录供应商响应全文，避免意外泄露密钥或合同内容。
        message=str(exc) if isinstance(exc,ValueError) else '审查失败：请检查模型配置、网络或文件内容后重试。错误类型：'+type(exc).__name__
        if settings.dashscope_api_key: message=message.replace(settings.dashscope_api_key,'[REDACTED]')
        repo.update_contract(cid,status='failed',error=message[:600])
        event(cid,'error',message[:600])
    finally:
        with lock: active.discard(cid)

@app.post('/api/contracts/{cid}/analyze',status_code=202)
def analyze(cid: str):
    with lock:
        item=get(cid)
        if cid in active: raise HTTPException(409,'该合同正在审查')
        if len(active)>=4: raise HTTPException(429,'当前审查任务较多，请稍后再试')
        if item['status']=='approved': raise HTTPException(409,'已审批合同不可覆盖，请重新上传新版本')
        active.add(cid); events[cid]=[]
        repo.update_contract(cid,status='reviewing',analysis={},review_note='',approval={},error='')
        event(cid,'queued','审查任务已提交')
        executor.submit(run_review,cid)
    return {'status':'reviewing'}

@app.get('/api/contracts/{cid}/events')
async def stream_events(cid: str):
    get(cid)
    async def stream():
        index=0
        while True:
            with lock: batch=list(events.get(cid,[])[index:])
            for e in batch:
                yield 'data: '+json.dumps(e,ensure_ascii=False)+'\n\n'
                index+=1
            if get(cid)['status']!='reviewing':
                yield 'event: complete\ndata: {}\n\n'
                break
            yield ': heartbeat\n\n'
            await asyncio.sleep(.5)
    return StreamingResponse(stream(),media_type='text/event-stream',headers={'Cache-Control':'no-cache','X-Accel-Buffering':'no'})

@app.post('/api/contracts/{cid}/review')
def review(cid: str,body: ReviewInput):
    with lock:
        item=get(cid)
        if item['status'] not in ['pending_review','confirmed','rejected']: raise HTTPException(409,'当前状态不允许人工复核')
        if len(body.note)>10000: raise HTTPException(422,'意见不能超过 10000 字')
        repo.update_contract(cid,status='confirmed' if body.decision=='confirm' else 'rejected',review_note=body.note)
    return detail(cid)

@app.post('/api/contracts/{cid}/approve')
def approve(cid: str,body: ApprovalInput):
    with lock:
        item=get(cid)
        if item['status']!='confirmed': raise HTTPException(409,'需先人工确认审查结果')
        approval={'id':'SIM-'+uuid.uuid4().hex[:10].upper(),'status':'approved','comment':body.comment,'at':datetime.now(timezone.utc).isoformat(),'system':'本地模拟审批，无外部系统回写'}
        repo.update_contract(cid,status='approved',approval=approval)
    return detail(cid)

@app.get('/api/contracts/{cid}/report')
def report(cid: str):
    item=get(cid)
    if not item['analysis']: raise HTTPException(409,'请先完成审查')
    filename=quote(Path(item['filename']).stem+'-审查报告.md')
    return Response(markdown_report(item),media_type='text/markdown; charset=utf-8',headers={'Content-Disposition':f"attachment; filename*=UTF-8''{filename}"})
