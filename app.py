import streamlit as st
import sqlite3, hashlib, secrets, re, shutil
from pathlib import Path
from datetime import datetime

BASE = Path(__file__).parent
DATA = BASE / 'data'
UPLOADS = DATA / 'uploads'
DB = DATA / 'jurisort.db'
UPLOADS.mkdir(parents=True, exist_ok=True)

PLANS = {
    'Solo': {'price': 599, 'cases': 5, 'docs': 250},
    'Office': {'price': 1199, 'cases': 15, 'docs': 1000},
    'Office+': {'price': 1999, 'cases': 30, 'docs': 2500},
}
CATEGORIES = ['Escritos de parte','Actuaciones judiciales','Pruebas','Resoluciones','Anexos','Sin clasificar']

def conn():
    c = sqlite3.connect(DB, check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c

def hash_pw(p, salt=None):
    salt = salt or secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac('sha256', p.encode(), salt.encode(), 200_000).hex()
    return f'{salt}${h}'

def check_pw(p, stored):
    salt, _ = stored.split('$', 1)
    return secrets.compare_digest(hash_pw(p, salt), stored)

def init_db():
    c=conn(); cur=c.cursor()
    cur.executescript('''
    CREATE TABLE IF NOT EXISTS firms(id INTEGER PRIMARY KEY, name TEXT NOT NULL, plan TEXT NOT NULL DEFAULT 'Solo', active INTEGER DEFAULT 1, created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, firm_id INTEGER, name TEXT NOT NULL, email TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'client', active INTEGER DEFAULT 1, created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS cases(id INTEGER PRIMARY KEY, firm_id INTEGER NOT NULL, internal_id TEXT NOT NULL, court_file TEXT, client_name TEXT, matter TEXT, court TEXT, parties TEXT, status TEXT DEFAULT 'Activo', created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS documents(id INTEGER PRIMARY KEY, case_id INTEGER NOT NULL, original_name TEXT NOT NULL, stored_name TEXT NOT NULL, doc_date TEXT, doc_type TEXT DEFAULT 'Sin clasificar', category TEXT DEFAULT 'Sin clasificar', pages INTEGER DEFAULT 0, confidence REAL DEFAULT 0, review_status TEXT DEFAULT 'Pendiente', notes TEXT, sha256 TEXT, uploaded_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS incidents(id INTEGER PRIMARY KEY, case_id INTEGER NOT NULL, document_id INTEGER, severity TEXT NOT NULL, kind TEXT NOT NULL, description TEXT NOT NULL, status TEXT DEFAULT 'Pendiente', created_at TEXT NOT NULL, resolved_at TEXT);
    CREATE TABLE IF NOT EXISTS timeline(id INTEGER PRIMARY KEY, case_id INTEGER NOT NULL, document_id INTEGER, event_date TEXT, event TEXT NOT NULL, verified INTEGER DEFAULT 0, created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY, user_id INTEGER, action TEXT NOT NULL, entity TEXT, entity_id INTEGER, detail TEXT, created_at TEXT NOT NULL);
    ''')
    # Bootstrap admin. Change immediately in production.
    if not cur.execute("SELECT 1 FROM users WHERE role='admin'").fetchone():
        cur.execute("INSERT INTO users(firm_id,name,email,password_hash,role,created_at) VALUES(NULL,?,?,?,?,?)",
                    ('Administrador Jurisort','admin@jurisort.local',hash_pw('Cambiar123!'),'admin',datetime.now().isoformat(timespec='seconds')))
    c.commit(); c.close()

def audit(user_id, action, entity='', entity_id=None, detail=''):
    c=conn(); c.execute('INSERT INTO audit(user_id,action,entity,entity_id,detail,created_at) VALUES(?,?,?,?,?,?)',
        (user_id,action,entity,entity_id,detail,datetime.now().isoformat(timespec='seconds'))); c.commit(); c.close()

def file_hash(path):
    h=hashlib.sha256()
    with open(path,'rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''): h.update(chunk)
    return h.hexdigest()

def infer_metadata(filename):
    """MVP rules only. Later replace/augment with OCR + approved AI provider."""
    n=filename.lower()
    rules=[
        ('Demanda inicial','Escritos de parte',['demanda']),
        ('Contestación','Escritos de parte',['contestacion','contestación']),
        ('Promoción','Escritos de parte',['promocion','promoción']),
        ('Auto admisorio','Actuaciones judiciales',['auto','admis']),
        ('Emplazamiento','Actuaciones judiciales',['emplaz']),
        ('Notificación','Actuaciones judiciales',['notific']),
        ('Audiencia','Actuaciones judiciales',['audiencia']),
        ('Prueba','Pruebas',['prueba','pericial','testimonial']),
        ('Sentencia/Resolución','Resoluciones',['sentencia','resolucion','resolución']),
        ('Anexo','Anexos',['anexo','contrato','identificacion','identificación']),
    ]
    for typ,cat,keys in rules:
        if any(k in n for k in keys): return typ,cat,.88
    return 'Por identificar','Sin clasificar',.25

def normalize_name(seq, date, typ, ext):
    safe=re.sub(r'[^A-Za-z0-9ÁÉÍÓÚáéíóúÑñ]+','_',typ).strip('_')
    d=date or 'SIN-FECHA'
    return f'{seq:03d}_{d}_{safe}{ext.lower()}'

def save_document(upload, case_id, user_id):
    c=conn(); cur=c.cursor()
    case=cur.execute('SELECT * FROM cases WHERE id=?',(case_id,)).fetchone()
    count=cur.execute('SELECT COUNT(*) n FROM documents WHERE case_id=?',(case_id,)).fetchone()['n']+1
    typ,cat,confidence=infer_metadata(upload.name)
    ext=Path(upload.name).suffix or '.bin'
    folder=UPLOADS/str(case['firm_id'])/str(case_id); folder.mkdir(parents=True,exist_ok=True)
    temp=folder/('original_'+secrets.token_hex(6)+ext)
    with open(temp,'wb') as f: f.write(upload.getbuffer())
    sha=file_hash(temp)
    duplicate=cur.execute('SELECT id,original_name FROM documents WHERE case_id=? AND sha256=?',(case_id,sha)).fetchone()
    stored=normalize_name(count,None,typ,ext)
    final=folder/stored
    shutil.copy2(temp,final)
    review='Aprobación requerida' if confidence < .80 else 'Pendiente de revisión'
    cur.execute('''INSERT INTO documents(case_id,original_name,stored_name,doc_type,category,confidence,review_status,sha256,uploaded_at)
                   VALUES(?,?,?,?,?,?,?,?,?)''',(case_id,upload.name,stored,typ,cat,confidence,review,sha,datetime.now().isoformat(timespec='seconds')))
    did=cur.lastrowid
    if duplicate:
        cur.execute('INSERT INTO incidents(case_id,document_id,severity,kind,description,created_at) VALUES(?,?,?,?,?,?)',
                    (case_id,did,'Media','Duplicado',f'Coincide exactamente con {duplicate["original_name"]}.',datetime.now().isoformat(timespec='seconds')))
    if confidence < .80:
        cur.execute('INSERT INTO incidents(case_id,document_id,severity,kind,description,created_at) VALUES(?,?,?,?,?,?)',
                    (case_id,did,'Media','Clasificación incierta','El sistema no pudo clasificar este archivo con suficiente confianza.',datetime.now().isoformat(timespec='seconds')))
    c.commit(); c.close(); audit(user_id,'UPLOAD','document',did,upload.name)

def sidebar():
    st.sidebar.markdown('## JURISORT')
    st.sidebar.caption('Control documental jurídico')
    if st.sidebar.button('Cerrar sesión'):
        st.session_state.clear(); st.rerun()

def login():
    st.title('JURISORT')
    st.caption('Tu expediente. En orden.')
    with st.form('login'):
        email=st.text_input('Correo').strip().lower(); pw=st.text_input('Contraseña',type='password')
        ok=st.form_submit_button('Ingresar',use_container_width=True)
    if ok:
        c=conn(); u=c.execute('SELECT * FROM users WHERE email=? AND active=1',(email,)).fetchone(); c.close()
        if u and check_pw(pw,u['password_hash']):
            st.session_state.user=dict(u); audit(u['id'],'LOGIN'); st.rerun()
        st.error('Credenciales incorrectas.')

def dashboard(user):
    c=conn(); firm_id=user['firm_id']
    if user['role']=='admin':
        firms=c.execute('SELECT COUNT(*) n FROM firms WHERE active=1').fetchone()['n']; cases=c.execute('SELECT COUNT(*) n FROM cases').fetchone()['n']; pending=c.execute("SELECT COUNT(*) n FROM incidents WHERE status='Pendiente'").fetchone()['n']; reviews=c.execute("SELECT COUNT(*) n FROM documents WHERE review_status!='Aprobado'").fetchone()['n']
        a,b,d,e=st.columns(4); a.metric('Despachos',firms); b.metric('Expedientes',cases); d.metric('Incidencias',pending); e.metric('Por revisar',reviews)
    else:
        cases=c.execute('SELECT COUNT(*) n FROM cases WHERE firm_id=?',(firm_id,)).fetchone()['n']; docs=c.execute('SELECT COUNT(*) n FROM documents d JOIN cases c ON d.case_id=c.id WHERE c.firm_id=?',(firm_id,)).fetchone()['n']; inc=c.execute("SELECT COUNT(*) n FROM incidents i JOIN cases c ON i.case_id=c.id WHERE c.firm_id=? AND i.status='Pendiente'",(firm_id,)).fetchone()['n']
        a,b,d=st.columns(3); a.metric('Expedientes',cases); b.metric('Documentos',docs); d.metric('Incidencias',inc)
    c.close()

def cases_page(user):
    c=conn();
    if user['role']=='admin': firms=c.execute('SELECT id,name FROM firms WHERE active=1 ORDER BY name').fetchall(); options={f['name']:f['id'] for f in firms}
    else: options={'Mi despacho':user['firm_id']}
    st.subheader('Expedientes')
    with st.expander('Nuevo expediente'):
        if not options: st.info('Primero crea un despacho desde Administración.')
        else:
            with st.form('newcase'):
                fname=st.selectbox('Despacho',list(options)); court_file=st.text_input('Número de expediente'); client=st.text_input('Cliente / referencia interna'); matter=st.selectbox('Materia',['Civil','Mercantil','Familiar','Penal','Laboral','Administrativo','Otra']); court=st.text_input('Órgano / juzgado'); parties=st.text_input('Partes'); submit=st.form_submit_button('Crear')
            if submit:
                fid=options[fname]; now=datetime.now().isoformat(timespec='seconds'); n=c.execute('SELECT COUNT(*) n FROM cases WHERE firm_id=?',(fid,)).fetchone()['n']+1; iid=f'JRS-{datetime.now().year}-{n:04d}'
                c.execute('INSERT INTO cases(firm_id,internal_id,court_file,client_name,matter,court,parties,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)',(fid,iid,court_file,client,matter,court,parties,now,now)); cid=c.execute('SELECT last_insert_rowid()').fetchone()[0]; c.commit(); audit(user['id'],'CREATE','case',cid,iid); st.success(f'Creado {iid}'); st.rerun()
    q='SELECT c.*, f.name firm_name FROM cases c JOIN firms f ON c.firm_id=f.id'; args=()
    if user['role']!='admin': q+=' WHERE c.firm_id=?'; args=(user['firm_id'],)
    rows=c.execute(q+' ORDER BY c.updated_at DESC',args).fetchall()
    for r in rows:
        with st.expander(f"{r['internal_id']} · {r['court_file'] or 'Sin número'} · {r['client_name'] or ''}"):
            st.caption(f"{r['firm_name']} · {r['matter']} · {r['court'] or 'Órgano no registrado'}")
            uploads=st.file_uploader('Agregar PDF/JPG/PNG',type=['pdf','jpg','jpeg','png'],accept_multiple_files=True,key=f"up{r['id']}")
            if st.button('Procesar archivos',key=f"proc{r['id']}",disabled=not uploads):
                for up in uploads: save_document(up,r['id'],user['id'])
                st.success(f'{len(uploads)} archivo(s) incorporados.'); st.rerun()
            docs=c.execute('SELECT * FROM documents WHERE case_id=? ORDER BY id',(r['id'],)).fetchall()
            for d in docs:
                cols=st.columns([3,2,1,2]); cols[0].write(d['stored_name']); cols[1].write(d['category']); cols[2].write(f"{int(d['confidence']*100)}%")
                newstatus=cols[3].selectbox('Revisión',['Pendiente de revisión','Aprobación requerida','Aprobado','Rechazado'],index=['Pendiente de revisión','Aprobación requerida','Aprobado','Rechazado'].index(d['review_status']) if d['review_status'] in ['Pendiente de revisión','Aprobación requerida','Aprobado','Rechazado'] else 0,key=f"s{d['id']}",label_visibility='collapsed')
                if newstatus!=d['review_status']:
                    c.execute('UPDATE documents SET review_status=? WHERE id=?',(newstatus,d['id'])); c.commit(); audit(user['id'],'REVIEW','document',d['id'],newstatus); st.rerun()
    c.close()

def incidents_page(user):
    c=conn(); q='''SELECT i.*,c.internal_id,d.stored_name FROM incidents i JOIN cases c ON i.case_id=c.id LEFT JOIN documents d ON i.document_id=d.id'''; args=()
    if user['role']!='admin': q+=' WHERE c.firm_id=?'; args=(user['firm_id'],)
    rows=c.execute(q+' ORDER BY CASE i.severity WHEN "Alta" THEN 1 WHEN "Media" THEN 2 ELSE 3 END, i.created_at DESC',args).fetchall()
    st.subheader('Centro de incidencias')
    if not rows: st.success('Sin incidencias registradas.')
    for x in rows:
        with st.expander(f"{x['severity']} · {x['internal_id']} · {x['kind']} · {x['status']}"):
            st.write(x['description']); st.caption(x['stored_name'] or 'Incidencia general')
            if user['role']=='admin' and x['status']=='Pendiente' and st.button('Marcar resuelta',key=f"i{x['id']}"):
                c.execute("UPDATE incidents SET status='Resuelta',resolved_at=? WHERE id=?",(datetime.now().isoformat(timespec='seconds'),x['id'])); c.commit(); audit(user['id'],'RESOLVE','incident',x['id']); st.rerun()
    c.close()

def admin_page(user):
    if user['role']!='admin': st.error('Acceso restringido.'); return
    c=conn(); st.subheader('Administración')
    tab1,tab2,tab3=st.tabs(['Despachos y planes','Usuarios','Auditoría'])
    with tab1:
        st.markdown('### Planes mensuales')
        cols=st.columns(3)
        for col,(name,p) in zip(cols,PLANS.items()):
            with col: st.markdown(f"**{name}**"); st.metric('MXN / mes',f"${p['price']:,}"); st.caption(f"Hasta {p['cases']} expedientes · {p['docs']} documentos/mes")
        with st.form('firm'):
            name=st.text_input('Nombre del despacho'); plan=st.selectbox('Plan',list(PLANS)); go=st.form_submit_button('Crear despacho')
        if go and name:
            c.execute('INSERT INTO firms(name,plan,created_at) VALUES(?,?,?)',(name,plan,datetime.now().isoformat(timespec='seconds'))); fid=c.execute('SELECT last_insert_rowid()').fetchone()[0]; c.commit(); audit(user['id'],'CREATE','firm',fid,name); st.rerun()
        for f in c.execute('SELECT * FROM firms ORDER BY id DESC').fetchall(): st.write(f"**{f['name']}** — {f['plan']}")
    with tab2:
        firms=c.execute('SELECT id,name FROM firms WHERE active=1').fetchall(); opts={f['name']:f['id'] for f in firms}
        if opts:
            with st.form('usr'):
                firm=st.selectbox('Despacho',list(opts)); name=st.text_input('Nombre'); email=st.text_input('Correo').lower().strip(); pw=st.text_input('Contraseña temporal',type='password'); go=st.form_submit_button('Crear usuario')
            if go and email and pw:
                try:
                    c.execute('INSERT INTO users(firm_id,name,email,password_hash,role,created_at) VALUES(?,?,?,?,?,?)',(opts[firm],name,email,hash_pw(pw),'client',datetime.now().isoformat(timespec='seconds'))); uid=c.execute('SELECT last_insert_rowid()').fetchone()[0]; c.commit(); audit(user['id'],'CREATE','user',uid,email); st.success('Usuario creado.')
                except sqlite3.IntegrityError: st.error('Ese correo ya existe.')
    with tab3:
        rows=c.execute('''SELECT a.*,u.email FROM audit a LEFT JOIN users u ON a.user_id=u.id ORDER BY a.id DESC LIMIT 100''').fetchall()
        st.dataframe([dict(r) for r in rows],use_container_width=True,hide_index=True)
    c.close()

def main():
    st.set_page_config(page_title='Jurisort',page_icon='⚖️',layout='wide')
    init_db()
    st.markdown('''<style>.block-container{max-width:1200px;padding-top:2rem}.stMetric{border:1px solid #e5e7eb;padding:14px;border-radius:12px}h1,h2,h3{letter-spacing:-.02em}</style>''',unsafe_allow_html=True)
    if 'user' not in st.session_state: login(); return
    u=st.session_state.user; sidebar()
    menu=['Inicio','Expedientes','Incidencias']+(['Administración'] if u['role']=='admin' else [])
    page=st.sidebar.radio('Navegación',menu)
    st.title(page)
    if page=='Inicio': dashboard(u)
    elif page=='Expedientes': cases_page(u)
    elif page=='Incidencias': incidents_page(u)
    else: admin_page(u)
    st.divider(); st.caption('Jurisort MVP · Herramienta de gestión documental. La clasificación automática requiere revisión humana y no sustituye criterio jurídico profesional.')

if __name__=='__main__': main()
