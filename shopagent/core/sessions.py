import json
import secrets
import time
from shopagent.storage import connect, encode, now
from shopagent.security import password_hash, check_password, digest
from shopagent.contracts.models import BusinessError, Identity


def initialize():
    with connect("core") as db:
        db.executescript("""CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY,password TEXT,role TEXT);
        CREATE TABLE IF NOT EXISTS tokens(hash TEXT PRIMARY KEY,user_id TEXT REFERENCES users(id),expires_at REAL);
        CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY,user_id TEXT,context TEXT,created_at TEXT);
        CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY AUTOINCREMENT,session_id TEXT REFERENCES sessions(id),role TEXT,content TEXT,result TEXT,created_at TEXT);
        CREATE TABLE IF NOT EXISTS tickets(id TEXT PRIMARY KEY,user_id TEXT,session_id TEXT,content TEXT,key TEXT UNIQUE,status TEXT,created_at TEXT);
        CREATE TABLE IF NOT EXISTS ticket_replies(request_key TEXT PRIMARY KEY,ticket_id TEXT REFERENCES tickets(id),admin_id TEXT,content TEXT,created_at TEXT);
        PRAGMA user_version=1;""")
        if 'last_read_id' not in {r['name'] for r in db.execute('PRAGMA table_info(sessions)')}:
            db.execute('ALTER TABLE sessions ADD COLUMN last_read_id INTEGER NOT NULL DEFAULT 0')
        db.execute('CREATE INDEX IF NOT EXISTS message_session ON messages(session_id,id)')
        db.execute('CREATE TABLE IF NOT EXISTS ticket_events(id INTEGER PRIMARY KEY,ticket_id TEXT,actor TEXT,old_status TEXT,new_status TEXT,created_at TEXT)')
        from shopagent.core.environment import production
        if production():
            return
        for name, role in (("alice", "buyer"), ("bob", "buyer"), ("admin", "admin")):
            if not db.execute("SELECT id FROM users WHERE id=?", (name,)).fetchone():
                db.execute("INSERT INTO users VALUES(?,?,?)", (name, password_hash("demo123"), role))


def login(username, password):
    from shopagent.core.environment import production
    if production() and (username in ('alice','bob','admin') or username.startswith('guest_')):
        raise BusinessError('LOGIN_FAILED','账号或密码错误。')
    with connect("core") as db:
        row = db.execute("SELECT * FROM users WHERE id=?", (username,)).fetchone()
        if not row or not check_password(password, row["password"]):
            raise BusinessError("LOGIN_FAILED", "账号或密码错误。")
        token = secrets.token_urlsafe(32)
        db.execute("INSERT INTO tokens VALUES(?,?,?)", (digest(token), username, time.time()+8*3600))
        return {"token": token, "user": {"user_id": username, "role": row["role"]}, "expires_in": 8*3600}


def authenticate(token):
    with connect("core") as db:
        row = db.execute("SELECT t.user_id,u.role FROM tokens t JOIN users u ON t.user_id=u.id WHERE t.hash=? AND t.expires_at>?", (digest(token), time.time())).fetchone()
    if not row:
        raise BusinessError("UNAUTHORIZED", "请重新登录，登录凭据可能已过期。")
    from shopagent.core.environment import production
    if production() and row['user_id'] in ('alice','bob','admin'):
        raise BusinessError('UNAUTHORIZED','示例账户不能用于生产环境。')
    return Identity(user_id=row["user_id"], role=row["role"])


def validate_production_admin():
    with connect('core') as db:
        rows=db.execute('SELECT * FROM users').fetchall()
    if any(r['id'] in ('alice','bob','admin') or check_password('demo123',r['password']) for r in rows):
        raise BusinessError('UNSAFE_ACCOUNT','生产库存在示例账户或示例密码，拒绝启动。')
    if not any(r['role']=='admin' for r in rows):
        raise BusinessError('ADMIN_REQUIRED','先运行正式管理员初始化，再启动生产模式。')


def initialize_admin(username,password):
    import re
    from shopagent.core.environment import production
    if not production():
        raise BusinessError('ENVIRONMENT_MISMATCH','正式管理员只能在production模式初始化。')
    if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{2,39}',username) or username in ('alice','bob','admin') or username.startswith('guest_'):
        raise BusinessError('INVALID_ADMIN','账号需为3至40位英文字母、数字、横线或下划线，不能使用示例账号或guest_前缀。')
    if not 12<=len(password)<=100 or not any(c.isalpha() for c in password) or not any(c.isdigit() for c in password):
        raise BusinessError('WEAK_PASSWORD','密码需为12至100位，至少包含字母和数字。')
    initialize()
    with connect('core') as db:
        db.execute('BEGIN IMMEDIATE')
        if db.execute("SELECT id FROM users WHERE role='admin'").fetchone():
            raise BusinessError('ADMIN_EXISTS','正式管理员已经初始化，不能覆盖原账号或重置密码。')
        db.execute('INSERT INTO users VALUES(?,?,?)',(username,password_hash(password),'admin'))
    return {'ok':True,'username':username}


def guest_login(notice_version=None):
    username = 'guest_' + secrets.token_hex(16)
    token = secrets.token_urlsafe(32)
    with connect('core') as db:
        db.execute('BEGIN IMMEDIATE')
        if notice_version is not None:
            from shopagent.core.maintenance import NOTICE_VERSION
            revision=db.execute('SELECT revision FROM retention_settings WHERE id=1').fetchone()[0]
            if notice_version!=NOTICE_VERSION+'-r'+str(revision):
                raise BusinessError('PRIVACY_REQUIRED','咨询说明已更新，请刷新并重新阅读。')
        db.execute('INSERT INTO users VALUES(?,?,?)', (username, password_hash(secrets.token_urlsafe(48)), 'buyer'))
        db.execute('INSERT INTO tokens VALUES(?,?,?)', (digest(token), username, time.time()+8*3600))
        if notice_version is not None:
            db.execute('INSERT INTO privacy_ack VALUES(?,?,?)',(username,notice_version,now()))
    return {'token': token, 'user': {'user_id':username,'role':'buyer'}, 'expires_in':8*3600}


def session(identity, session_id=None):
    with connect("core") as db:
        if session_id:
            row = db.execute("SELECT * FROM sessions WHERE id=? AND user_id=?", (session_id, identity.user_id)).fetchone()
            if not row:
                raise BusinessError("NOT_FOUND", "会话不存在或不属于当前用户。")
            return row["id"], json.loads(row["context"])
        sid = secrets.token_urlsafe(16)
        db.execute("INSERT INTO sessions(id,user_id,context,created_at) VALUES(?,?,?,?)", (sid, identity.user_id, "{}", now()))
        return sid, {}


def save(sid, identity, context, question, result):
    with connect("core") as db:
        if not db.execute("SELECT id FROM sessions WHERE id=? AND user_id=?", (sid, identity.user_id)).fetchone():
            raise BusinessError("FORBIDDEN", "不能写入其他用户会话。")
        db.execute("UPDATE sessions SET context=? WHERE id=?", (encode(context), sid))
        db.execute("INSERT INTO messages(session_id,role,content,result,created_at) VALUES(?,?,?,?,?)", (sid, "user", question, "{}", now()))
        # 确认凭据是短期敏感信息，不进入持久化聊天历史。
        clean = json.loads(encode(result))
        if clean.get("draft"):
            clean["draft"].pop("confirmation_token", None)
        db.execute("INSERT INTO messages(session_id,role,content,result,created_at) VALUES(?,?,?,?,?)", (sid, "assistant", result["reply"], encode(clean), now()))


def history(sid, identity):
    session(identity, sid)
    with connect("core") as db:
        return [{**dict(row), "result": json.loads(row["result"])} for row in db.execute("SELECT id,role,content,result,created_at FROM messages WHERE session_id=? ORDER BY id", (sid,))]


def mark_read(sid, identity, message_id):
    session(identity,sid)
    with connect('core') as db:
        if message_id and not db.execute('SELECT id FROM messages WHERE id=? AND session_id=?',(message_id,sid)).fetchone():
            raise BusinessError('NOT_FOUND','消息不属于该会话。')
        db.execute('UPDATE sessions SET last_read_id=max(last_read_id,?) WHERE id=? AND user_id=?',(message_id,sid,identity.user_id))
    return {'ok':True}


def customer_tickets(identity):
    with connect('core') as db:
        rows = db.execute('SELECT t.*,s.last_read_id FROM tickets t JOIN sessions s ON s.id=t.session_id AND s.user_id=t.user_id WHERE t.user_id=? ORDER BY t.created_at DESC LIMIT 100',(identity.user_id,)).fetchall()
        values=[]
        for row in rows:
            replies=db.execute("SELECT id FROM messages WHERE session_id=? AND role='assistant' AND id>? AND json_extract(result,'$.source')='human' AND json_extract(result,'$.ticket_id')=?",(row['session_id'],row['last_read_id'],row['id'])).fetchall()
            values.append({k:row[k] for k in ('id','session_id','content','status','created_at')} | {'unread':len(replies)})
        return values


def ticket_detail(identity, ticket_id):
    if identity.role!='admin':
        raise BusinessError('FORBIDDEN','只有商家可以查看咨询详情。')
    with connect('core') as db:
        row=db.execute('SELECT id,user_id,session_id,content,status,created_at FROM tickets WHERE id=?',(ticket_id,)).fetchone()
        if not row:
            raise BusinessError('NOT_FOUND','咨询不存在。')
        value=dict(row)
        owner=Identity(user_id=value['user_id'])
        value['events']=[dict(r) for r in db.execute('SELECT old_status,new_status,created_at FROM ticket_events WHERE ticket_id=? ORDER BY id',(ticket_id,))]
    value['messages']=history(value['session_id'],owner)[-20:]
    return value


def ticket_status(identity, ticket_id, expected_status, status):
    if identity.role!='admin':
        raise BusinessError('FORBIDDEN','只有商家可以处理咨询。')
    with connect('core') as db:
        db.execute('BEGIN IMMEDIATE')
        row=db.execute('SELECT status FROM tickets WHERE id=?',(ticket_id,)).fetchone()
        if not row:
            raise BusinessError('NOT_FOUND','咨询不存在。')
        if row['status']!=expected_status:
            raise BusinessError('TICKET_CONFLICT','咨询状态已经变化，请刷新。')
        db.execute('UPDATE tickets SET status=? WHERE id=?',(status,ticket_id))
        db.execute('INSERT INTO ticket_events(ticket_id,actor,old_status,new_status,created_at) VALUES(?,?,?,?,?)',(ticket_id,identity.user_id,expected_status,status,now()))
    return {'ok':True,'status':status}


def ticket(identity, sid, content):
    key = digest(identity.user_id + sid + content)
    with connect("core") as db:
        db.execute("INSERT OR IGNORE INTO tickets VALUES(?,?,?,?,?,?,?)", ("T-"+secrets.token_hex(6).upper(), identity.user_id, sid, content, key, "待处理", now()))
        return dict(db.execute("SELECT * FROM tickets WHERE key=?", (key,)).fetchone())


def reply_ticket(identity, ticket_id, content, request_key):
    if identity.role != "admin":
        raise BusinessError("FORBIDDEN", "只有管理员可以回复咨询。")
    content = content.strip()
    if not content:
        raise BusinessError("INVALID_REPLY", "请输入回复内容。")
    with connect("core") as db:
        db.execute("BEGIN IMMEDIATE")
        previous = db.execute("SELECT * FROM ticket_replies WHERE request_key=?", (request_key,)).fetchone()
        if previous:
            if (previous["ticket_id"], previous["admin_id"], previous["content"]) != (ticket_id, identity.user_id, content):
                raise BusinessError("REPLY_CONFLICT", "同一个提交编号不能用于不同回复。")
            return {"ok": True, "ticket_id": ticket_id, "duplicate": True}
        value = db.execute("SELECT * FROM tickets WHERE id=?", (ticket_id,)).fetchone()
        if not value:
            raise BusinessError("NOT_FOUND", "咨询不存在。")
        if value['status']=='已关闭':
            raise BusinessError('TICKET_CLOSED','咨询已经关闭，请先重新打开再回复。')
        if not db.execute("SELECT id FROM sessions WHERE id=? AND user_id=?", (value["session_id"], value["user_id"])).fetchone():
            raise BusinessError("NOT_FOUND", "咨询会话不存在。")
        created = now()
        db.execute("INSERT INTO ticket_replies VALUES(?,?,?,?,?)", (request_key, ticket_id, identity.user_id, content, created))
        result = {"reply": content, "source": "human", "ticket_id": ticket_id, "mode": "human"}
        db.execute("INSERT INTO messages(session_id,role,content,result,created_at) VALUES(?,?,?,?,?)", (value["session_id"], "assistant", content, encode(result), created))
        db.execute("UPDATE tickets SET status=? WHERE id=?", ("已回复", ticket_id))
        return {"ok": True, "ticket_id": ticket_id, "duplicate": False}
