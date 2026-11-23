import time
import pytest
from fastapi.testclient import TestClient
from backend.app import main
from backend.app.repository import Repository
from backend.app.config import settings

@pytest.fixture
def client(tmp_dir,monkeypatch):
    monkeypatch.setattr(main,'repo',Repository(tmp_dir/'test.sqlite3'))
    folder=tmp_dir/'uploads';folder.mkdir()
    monkeypatch.setattr(main,'uploads',folder)
    monkeypatch.setattr(settings,'dashscope_api_key','')
    with TestClient(main.app) as c: yield c

def test_lifespan_marks_interrupted_review_failed(tmp_dir,monkeypatch):
    repo=Repository(tmp_dir/'recover.sqlite3')
    cid=repo.create_contract('interrupted.txt','interrupted.txt')['id']
    repo.update_contract(cid,status='reviewing')
    folder=tmp_dir/'uploads';folder.mkdir()
    monkeypatch.setattr(main,'repo',repo)
    monkeypatch.setattr(main,'uploads',folder)
    with TestClient(main.app) as c:
        item=c.get('/api/contracts/'+cid).json()
    assert item['status']=='failed'
    assert '中断' in item['error']

def upload(client,text='采购合同\n甲方签约后支付全部货款。乙方承担一切损失。'):
    res=client.post('/api/contracts',files={'file':('sample.txt',text.encode(),'text/plain')})
    assert res.status_code==201
    return res.json()['id']

def wait_result(client,cid):
    for _ in range(100):
        c=client.get('/api/contracts/'+cid).json()
        if c['status']!='reviewing': return c
        time.sleep(.05)
    pytest.fail('review timed out')

def test_complete_lifecycle(client):
    cid=upload(client);base='/api/contracts/'+cid
    assert client.post(base+'/approve',json={'comment':'x'}).status_code==409
    assert client.get(base+'/report').status_code==409
    assert client.post(base+'/analyze').status_code==202
    result=wait_result(client,cid)
    assert result['status']=='pending_review',result
    assert result['analysis']['risks']
    assert 'stored_path' not in result
    events=client.get(base+'/events')
    assert 'event: complete' in events.text and 'validate' in events.text
    assert client.post(base+'/review',json={'decision':'reject','note':'补充验收'}).json()['status']=='rejected'
    assert client.post(base+'/approve',json={}).status_code==409
    assert client.post(base+'/analyze').status_code==202
    result=wait_result(client,cid)
    assert result['review_note']==''
    assert client.post(base+'/review',json={'decision':'confirm','note':'已人工核实并修订意见'}).status_code==200
    approval=client.post(base+'/approve',json={'comment':'测试模拟审批'}).json()
    assert approval['status']=='approved' and approval['approval']['id'].startswith('SIM-')
    report=client.get(base+'/report')
    assert report.status_code==200 and '已人工核实并修订意见' in report.text and 'RULE-PAY' in report.text
    assert client.post(base+'/analyze').status_code==409
    assert client.post(base+'/review',json={'decision':'reject','note':''}).status_code==409

def test_bad_uploads_and_parse_failure(client):
    assert client.post('/api/contracts',files={'file':('x.exe',b'x')}).status_code==415
    assert client.post('/api/contracts',files={'file':('x.txt',b'')}).status_code==400
    assert client.get('/api/contracts/missing').status_code==404
    cid=client.post('/api/contracts',files={'file':('broken.pdf',b'not a pdf')}).json()['id']
    client.post(f'/api/contracts/{cid}/analyze')
    result=wait_result(client,cid)
    assert result['status']=='failed' and result['error']
