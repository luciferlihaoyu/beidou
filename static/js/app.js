/* ===== 墨韵 · 小说写作器 — SPA Client ===== */
(function(){
'use strict';

// ========== API ==========
var API = {
  headers: function() {
    var h={'Content-Type':'application/json'};
    if(S.token) h['Authorization']='Bearer '+S.token;
    return h;
  },
  get: function(p) { return fetch(p,{headers:API.headers()}).then(function(r){ if(r.status===401){logout();throw Error('登录已过期');} if(!r.ok)throw Error('HTTP '+r.status); return r.json(); }); },
  post: function(p,d) { return fetch(p,{method:'POST',headers:API.headers(),body:JSON.stringify(d)}).then(function(r){ if(r.status===401){logout();throw Error('登录已过期');} if(!r.ok)throw Error('HTTP '+r.status); return r.json(); }); },
  put: function(p,d) { return fetch(p,{method:'PUT',headers:API.headers(),body:JSON.stringify(d)}).then(function(r){ if(r.status===401){logout();throw Error('登录已过期');} if(!r.ok)throw Error('HTTP '+r.status); return r.json(); }); },
  del: function(p) { return fetch(p,{method:'DELETE',headers:API.headers()}).then(function(r){ if(r.status===401){logout();throw Error('登录已过期');} if(!r.ok)throw Error('HTTP '+r.status); return r.json(); }); }
};

// ========== State ==========
var S = {
  theme: localStorage.getItem('moyun-theme')||'light',
  token: localStorage.getItem('moyun-token')||null,
  user: null,
  books: [],
  currentBook: null,
  currentChapter: null,
  aiProvider: 'volcengine',
  aiModel: null,
  aiProviders: [],     // 来自 /api/ai/providers
  aiChat: [],
  autoSaveTimer: null,
  _aiResult: ''
};

// ========== Utilities ==========
function esc(s){ if(!s)return''; var d=document.createElement('div'); d.textContent=s; return d.innerHTML; }
function fmt(n){ return n ? Number(n).toLocaleString() : '0'; }

function toast(msg,err){
  var t=document.createElement('div'); t.className='toast'+(err?' error':'');
  t.textContent=msg; document.body.appendChild(t); setTimeout(function(){ t.remove(); },2800);
}

function showModal(title,html,btns){
  btns=btns||[];
  var ov=document.createElement('div'); ov.className='modal-overlay';
  ov.onclick=function(e){ if(e.target===ov)ov.remove(); };
  var s=document.createElement('div'); s.className='modal-sheet';
  var ahtml='';
  btns.forEach(function(b){
    var cls=b.primary?'btn-primary':'btn-secondary';
    ahtml+='<button class="btn '+cls+'" data-action="'+b.idx+'">'+b.label+'</button>';
  });
  s.innerHTML='<h3>'+title+'</h3><div class="modal-body">'+html+'</div><div class="modal-actions">'+ahtml+'</div>';
  btns.forEach(function(b,i){
    var btn=s.querySelector('[data-action="'+i+'"]');
    if(btn) btn.onclick=function(){ b.onClick(); ov.remove(); };
    b.idx=i;
  });
  ov.appendChild(s); document.body.appendChild(ov); return ov;
}

// ========== Theme ==========
function applyTheme(){
  document.documentElement.setAttribute('data-theme', S.theme);
  localStorage.setItem('moyun-theme', S.theme);
}
function toggleTheme(){
  S.theme = S.theme==='dark' ? 'light' : 'dark';
  applyTheme(); render();
}

// ========== Router ==========
function nav(hash){ window.location.hash=hash; render(); }
function getRoute(){
  var h=window.location.hash.slice(1)||'home';
  var p=h.split('/');
  return {page:p[0],params:p.slice(1)};
}

// ========== Bottom Nav ==========
function bottomNav(active){
  var tabs=[
    {id:'home',label:'书库',icon:'📚'},
    {id:'write',label:'创作',icon:'✏️'},
    {id:'ai',label:'AI助手',icon:'🤖'},
    {id:'me',label:'我的',icon:'👤'}
  ];
  var html='<nav class="bottom-nav">';
  tabs.forEach(function(t){
    html+='<button class="bottom-nav-item'+(active===t.id?' active':'')+'" onclick="nav(\''+t.id+'\')">'+t.icon+'<span>'+t.label+'</span></button>';
  });
  html+='</nav>';
  return html;
}

// ========== Top Nav ==========
function topNav(title,showBack,actions){
  return '<nav class="top-nav">'+
    (showBack?'<button class="nav-btn" onclick="history.back()">←</button>':'')+
    '<h1>'+title+'</h1>'+
    (actions||'')+
    '</nav>';
}

// ========== MAIN RENDER ==========
function render(){
  try{ _doRender(); }catch(e){ document.getElementById('app').innerHTML='<div class="empty-state"><p>加载失败: '+e.message+'</p></div>'; }
}

function _doRender(){
  // Auth check - skip for login/register pages
  if(!S.token || !S.user){
    checkAuth(function(loggedIn){
      if(!loggedIn){
        var r=getRoute();
        if(r.page==='register'){
          registerPage(function(h){ setPage('注册',false,h); });
        } else {
          loginPage(function(h){ setPage('登录',false,h); });
        }
        return;
      }
      _routePage();
    });
    return;
  }
  _routePage();
}

function _routePage(){
  var r=getRoute();
  var page=r.page, params=r.params;
  var title='墨韵', showBack=false, noBottom=false;

  switch(page){
    case 'home': title='书库'; homePage(function(h){ setPage(title,false,h); }); return;
    case 'book': title='作品'; showBack=true; bookPage(parseInt(params[0]),function(h){ setPage(title,true,h); }); return;
    case 'editor': title=''; noBottom=true; editorPage(parseInt(params[0]),params[1]?parseInt(params[1]):0,function(h){ setEditorPage(h); }); return;
    case 'ai': title='AI助手'; aiPage(function(h){ setPage(title,false,h); }); return;
    case 'me': title='我的'; mePage(function(h){ setPage(title,false,h); }); return;
    case 'write': title='创作'; writePage(function(h){ setPage(title,false,h); }); return;
    default: setPage('404',false,'<div class="empty-state"><p>页面未找到</p></div>');
  }
}

function checkAuth(cb){
  if(!S.token){ cb(false); return; }
  fetch('/api/auth/me',{headers:{'Authorization':'Bearer '+S.token}})
    .then(function(r){
      if(!r.ok){ S.token=null; localStorage.removeItem('moyun-token'); cb(false); return; }
      return r.json();
    })
    .then(function(data){
      if(data&&data.username){ S.user=data; cb(true); }
      else { S.token=null; localStorage.removeItem('moyun-token'); cb(false); }
    })
    .catch(function(){ S.token=null; localStorage.removeItem('moyun-token'); cb(false); });
}

function logout(){
  S.token=null; S.user=null;
  localStorage.removeItem('moyun-token');
  render();
}

function setPage(title,showBack,content){
  var r=getRoute();
  var app=document.getElementById('app');
  app.innerHTML='<div class="app-container">'+
    topNav(title,showBack)+
    '<div class="page-content">'+content+'</div>'+
    bottomNav(r.page)+
    '</div>';
  attachPageEvents(r);
}

function setEditorPage(content){
  var app=document.getElementById('app');
  app.innerHTML='<div class="app-container" style="padding-bottom:0">'+content+'</div>';
  attachEditorEvents();
}

function attachPageEvents(r){
  if(r.page==='home') attachHome();
  if(r.page==='ai') attachAI();
}

// ========== HOME ==========
function homePage(cb){
  API.get('/api/books').then(function(books){
    S.books=books;
    if(!books.length){
      cb('<div class="empty-state"><div style="font-size:64px;margin-bottom:16px">📚</div><h3>开始你的创作之旅</h3><p style="margin-top:8px">还没有作品？点击下方按钮创建</p></div><button class="fab" onclick="createBook()">+</button>');
      return;
    }
    var html='<div class="search-bar"><span>🔍</span><input id="search-books" placeholder="搜索作品..." oninput="filterBooks()"></div>';
    books.forEach(function(b){
      html+='<div class="book-card" onclick="nav(\'book/'+b.id+'\')" data-title="'+esc(b.title)+'" data-author="'+esc(b.author||'')+'">';
      html+='<div class="book-cover">'+(b.cover_url?'<img src="'+b.cover_url+'" alt="">':'📖')+'</div>';
      html+='<div class="book-info">';
      html+='<h3>'+esc(b.title)+'</h3>';
      if(b.author) html+='<div class="author">'+esc(b.author)+'</div>';
      html+='<div class="book-meta"><span>📝 '+(b.chapter_count||0)+'章</span><span>📊 '+fmt(b.word_count)+'字</span></div>';
      html+='<span class="book-status'+(b.status==='已完结'?' finished':'')+'">'+(b.status||'连载中')+'</span>';
      html+='</div></div>';
    });
    html+='<button class="fab" onclick="createBook()">+</button>';
    cb(html);
  }).catch(function(e){ cb('<div class="empty-state"><p>加载失败</p></div>'); });
}

function attachHome(){ /* search inline */ }

function filterBooks(){
  var q=(document.getElementById('search-books')?.value||'').toLowerCase();
  document.querySelectorAll('.book-card').forEach(function(c){
    var t=(c.dataset.title||'').toLowerCase();
    var a=(c.dataset.author||'').toLowerCase();
    c.style.display = (q && !t.includes(q) && !a.includes(q)) ? 'none' : '';
  });
}

function createBook(){
  showModal('新建作品',
    '<div class="form-group"><label class="form-label">书名 *</label><input class="form-input" id="cb-title" placeholder="输入书名..." autofocus></div>'+
    '<div class="form-group"><label class="form-label">笔名</label><input class="form-input" id="cb-author" placeholder="作者笔名"></div>'+
    '<div class="form-group"><label class="form-label">简介</label><textarea class="form-textarea" id="cb-desc" placeholder="作品简介..." rows="3"></textarea></div>',
    [{label:'取消'},{label:'创建',primary:true,onClick:function(){
      var t=document.getElementById('cb-title').value.trim();
      if(!t){toast('请输入书名',true);return;}
      API.post('/api/books',{title:t,author:document.getElementById('cb-author').value.trim(),description:document.getElementById('cb-desc').value.trim()})
        .then(function(){ toast('创建成功！'); render(); })
        .catch(function(e){ toast(e.message,true); });
    }}]
  );
}

// ========== BOOK DETAIL ==========
function bookPage(bookId,cb){
  API.get('/api/books/'+bookId).then(function(b){
    S.currentBook=b;
    var vols=b.volumes||[], chs=b.chapters||[];
    var progress=b.chapter_count ? Math.round((chs.filter(function(c){return c.word_count>0;}).length/Math.max(b.chapter_count,1))*100) : 0;

    setTimeout(function(){
      var h1=document.querySelector('.top-nav h1'); if(h1)h1.textContent=b.title;
    },0);

    var html='<div class="detail-header">';
    html+='<div class="detail-cover">'+(b.cover_url?'<img src="'+b.cover_url+'" alt="">':'📖')+'</div>';
    html+='<div class="detail-info">';
    html+='<h2>'+esc(b.title)+'</h2>';
    if(b.author) html+='<div class="author">'+esc(b.author)+'</div>';
    html+='<div class="detail-stats">';
    html+='<div class="stat-item"><div class="value">'+(b.chapter_count||0)+'</div><div class="label">章节</div></div>';
    html+='<div class="stat-item"><div class="value">'+fmt(b.word_count)+'</div><div class="label">字数</div></div>';
    html+='<div class="stat-item"><div class="value">'+(b.volume_count||0)+'</div><div class="label">分卷</div></div>';
    html+='</div>';
    html+='<div class="progress-bar"><div class="progress-bar-fill" style="width:'+progress+'%"></div></div>';
    html+='<div style="font-size:11px;color:var(--text-muted);margin-top:2px">创作进度 '+progress+'%</div>';
    html+='</div></div>';

    html+='<div class="action-bar">';
    html+='<button class="btn btn-primary" onclick="startReading('+b.id+')">📖 开始阅读</button>';
    html+='<button class="btn btn-outline" onclick="addChapter('+b.id+')">+ 新建章节</button>';
    html+='</div>';
    html+='<div class="action-bar" style="padding-top:0">';
    html+='<button class="btn btn-outline" onclick="addVolume('+b.id+')">📂 新建分卷</button>';
    html+='<button class="btn btn-outline" onclick="nav(\'ai\')">🤖 AI 助手</button>';
    html+='</div>';

    vols.forEach(function(v){
      var vchs=chs.filter(function(c){return c.volume_id===v.id;});
      html+='<div class="volume-group">';
      html+='<div class="volume-header">📂 '+esc(v.title)+' <span style="font-weight:400;color:var(--text-muted);font-size:12px">('+(v.chapter_count||0)+'章)</span>';
      html+='<div style="flex:1"></div>';
      html+='<button class="btn btn-small btn-outline" onclick="event.stopPropagation();addChapter('+b.id+','+v.id+')" style="font-size:11px">添章</button>';
      html+='</div>';
      vchs.forEach(function(c){
        html+=chapterRow(c,b.id);
      });
      html+='</div>';
    });

    var orphan=chs.filter(function(c){return !c.volume_id;});
    if(orphan.length){
      html+='<div class="volume-group"><div class="volume-header">📄 未分卷</div>';
      orphan.forEach(function(c){ html+=chapterRow(c,b.id); });
      html+='</div>';
    }

    html+='<div class="action-bar" style="padding-top:16px">';
    html+='<button class="btn btn-outline" onclick="editBook('+b.id+')">✏️ 编辑信息</button>';
    html+='<button class="btn btn-danger" onclick="deleteBookConfirm('+b.id+')">🗑 删除</button>';
    html+='</div>';

    cb(html);
  }).catch(function(e){ cb('<div class="empty-state"><p>加载失败</p></div>'); });
}

function chapterRow(c,bookId){
  return '<div class="chapter-item" onclick="nav(\'editor/'+bookId+'/'+c.id+'\')">'+
    '<span class="ch-num">'+(c.sort_order!=null?c.sort_order+1:'')+'</span>'+
    '<span class="ch-title">'+esc(c.title)+'</span>'+
    '<span class="ch-words">'+fmt(c.word_count)+'字</span></div>';
}

function startReading(bookId){
  var b=S.currentBook; if(!b)return;
  var chs=b.chapters||[];
  if(chs.length){ nav('editor/'+bookId+'/'+chs[0].id); }
  else{ addChapter(bookId); }
}

function addChapter(bookId,volumeId){
  var count=(S.currentBook&&S.currentBook.chapter_count)||0;
  showModal('新建章节',
    '<div class="form-group"><label class="form-label">章节标题</label><input class="form-input" id="ach-title" value="第'+(count+1)+'章" autofocus></div>',
    [{label:'取消'},{label:'创建并写作',primary:true,onClick:function(){
      var t=document.getElementById('ach-title').value.trim()||'第'+(count+1)+'章';
      API.post('/api/books/'+bookId+'/chapters',{title:t,volume_id:volumeId})
        .then(function(r){ toast('已创建'); nav('editor/'+bookId+'/'+r.id); })
        .catch(function(e){ toast(e.message,true); });
    }}]
  );
}

function addVolume(bookId){
  showModal('新建分卷',
    '<div class="form-group"><label class="form-label">分卷名称</label><input class="form-input" id="av-title" placeholder="第1卷：启程" autofocus></div>'+
    '<div class="form-group"><label class="form-label">描述</label><input class="form-input" id="av-desc" placeholder="本卷概要..."></div>',
    [{label:'取消'},{label:'创建',primary:true,onClick:function(){
      var t=document.getElementById('av-title').value.trim();
      if(!t){toast('请输入名称',true);return;}
      API.post('/api/books/'+bookId+'/volumes',{title:t,description:document.getElementById('av-desc').value.trim()})
        .then(function(){ toast('已创建'); render(); })
        .catch(function(e){ toast(e.message,true); });
    }}]
  );
}

function editBook(bookId){
  var b=S.currentBook;
  var html='<div class="form-group"><label class="form-label">书名</label><input class="form-input" id="eb-title" value="'+esc(b.title)+'"></div>';
  html+='<div class="form-group"><label class="form-label">笔名</label><input class="form-input" id="eb-author" value="'+esc(b.author||'')+'"></div>';
  html+='<div class="form-group"><label class="form-label">简介</label><textarea class="form-textarea" id="eb-desc" rows="3">'+(b.description||'')+'</textarea></div>';
  html+='<div class="form-group"><label class="form-label">状态</label><select class="form-select" id="eb-status">';
  html+='<option value="连载中"'+(b.status==='连载中'?' selected':'')+'>连载中</option>';
  html+='<option value="已完结"'+(b.status==='已完结'?' selected':'')+'>已完结</option>';
  html+='<option value="停更"'+(b.status==='停更'?' selected':'')+'>停更</option>';
  html+='</select></div>';
  showModal('编辑作品',html,[
    {label:'取消'},
    {label:'保存',primary:true,onClick:function(){
      API.put('/api/books/'+bookId,{
        title:document.getElementById('eb-title').value.trim(),
        author:document.getElementById('eb-author').value.trim(),
        description:document.getElementById('eb-desc').value.trim(),
        status:document.getElementById('eb-status').value
      }).then(function(){ toast('已保存'); render(); })
        .catch(function(e){ toast(e.message,true); });
    }}
  ]);
}

function deleteBookConfirm(bookId){
  var title=esc(S.currentBook&&S.currentBook.title||'');
  showModal('确认删除',
    '<p style="text-align:center;color:var(--danger)">此操作不可恢复，确定删除《'+title+'》吗？</p>',
    [{label:'取消'},{label:'确认删除',primary:true,onClick:function(){
      API.del('/api/books/'+bookId)
        .then(function(){ toast('已删除'); nav('home'); })
        .catch(function(e){ toast(e.message,true); });
    }}]
  );
}

// ========== EDITOR ==========
function editorPage(bookId,chapterId,cb){
  var loadChapter = chapterId>0 ? API.get('/api/chapters/'+chapterId) : Promise.resolve({id:0,title:'',content:''});
  var loadBook = API.get('/api/books/'+bookId).catch(function(){ return null; });

  Promise.all([loadBook,loadChapter]).then(function(results){
    S.currentBook=results[0]; S.currentChapter=results[1];
    var b=S.currentBook||{},ch=S.currentChapter;
    var wc=(ch.content||'').replace(/\s/g,'').length;

    var html='<div class="editor-container">';
    html+=topNav(b.title||'写作',true,'<button class="nav-btn" onclick="saveNow()" title="保存">💾</button>');
    html+='<input class="editor-title" id="et-title" value="'+esc(ch.title)+'" placeholder="章节标题">';
    html+='<textarea class="editor-content" id="et-content" placeholder="开始写作...">'+(ch.content||'')+'</textarea>';
    html+='<button id="ai-toggle-btn" class="fab" style="bottom:120px" onclick="toggleAIPanel()" title="AI辅助">🤖</button>';
    html+='<div class="editor-toolbar">';
    html+='<span class="word-count" id="live-wc">'+fmt(wc)+' 字</span>';
    ['，','。','！','？','……','""','——'].forEach(function(m){
      html+='<button class="quick-mark" onclick="insertMark(\''+m+'\')">'+m+'</button>';
    });
    html+='</div>';
    html+='<div id="editor-ai" style="display:none;position:fixed;bottom:80px;left:50%;transform:translateX(-50%);width:100%;max-width:480px;z-index:55">';
    html+='<div class="ai-panel">';
    html+='<div class="ai-panel-header"><h4>✨ AI 辅助</h4><button class="nav-btn" onclick="toggleAIPanel()">✕</button></div>';
    html+='<div class="ai-chips">';
    html+='<button class="ai-chip" onclick="editorAI(\'expand\')">📝 扩写</button>';
    html+='<button class="ai-chip" onclick="editorAI(\'polish\')">✨ 润色</button>';
    html+='<button class="ai-chip" onclick="editorAI(\'continue\')">▶️ 续写</button>';
    html+='</div>';
    html+='<input class="form-input" id="ai-extra" placeholder="额外要求（可选）..." style="margin-bottom:8px">';
    html+='<div id="ai-loading" style="display:none;color:var(--accent);font-size:13px;padding:4px 0">⏳ AI 生成中...</div>';
    html+='<div id="ai-result-area" style="display:none">';
    html+='<div class="ai-result" id="ai-result-text"></div>';
    html+='<div class="ai-result-actions"><button class="btn btn-primary" onclick="insertAIResult()" style="width:100%">📥 插入正文</button></div>';
    html+='</div></div></div></div>';
    cb(html);
  }).catch(function(e){ cb('<div class="editor-container"><p>加载失败: '+e.message+'</p></div>'); });
}

function attachEditorEvents(){
  var ta=document.getElementById('et-content'); if(!ta)return;
  ta.addEventListener('input',function(){
    var wc=ta.value.replace(/\s/g,'').length;
    var wcEl=document.getElementById('live-wc'); if(wcEl)wcEl.textContent=fmt(wc)+' 字';
    if(S.autoSaveTimer)clearTimeout(S.autoSaveTimer);
    S.autoSaveTimer=setTimeout(function(){ save(true); },3000);
  });
  ta.addEventListener('keydown',function(e){
    if((e.ctrlKey||e.metaKey)&&e.key==='s'){e.preventDefault();saveNow();}
  });
}

function save(silent){
  var title=document.getElementById('et-title')?.value||'';
  var content=document.getElementById('et-content')?.value||'';
  var r=getRoute();
  var bookId=parseInt(r.params[0]);
  var chapterId=S.currentChapter?.id||0;

  var p;
  if(chapterId>0){
    p=API.put('/api/chapters/'+chapterId,{title:title,content:content});
  }else{
    p=API.post('/api/books/'+bookId+'/chapters',{title:title,content:content}).then(function(res){
      S.currentChapter={id:res.id,title:title,content:content};
      window.history.replaceState(null,'','#editor/'+bookId+'/'+res.id);
    });
  }
  p.then(function(){ if(!silent)toast('已保存 ✅'); })
   .catch(function(e){ if(!silent)toast(e.message,true); });
}

function saveNow(){ save(false); }

function insertMark(m){
  var ta=document.getElementById('et-content'); if(!ta)return;
  var s=ta.selectionStart,e=ta.selectionEnd;
  ta.value=ta.value.slice(0,s)+m+ta.value.slice(e);
  ta.selectionStart=ta.selectionEnd=s+m.length;
  ta.focus(); ta.dispatchEvent(new Event('input'));
}

function toggleAIPanel(){
  var p=document.getElementById('editor-ai');
  if(p)p.style.display=p.style.display==='none'?'block':'none';
}

function editorAI(action){
  var content=document.getElementById('et-content')?.value||'';
  var extra=document.getElementById('ai-extra')?.value||'';
  var loading=document.getElementById('ai-loading');
  var result=document.getElementById('ai-result-area');

  if(!content&&action!=='continue'){toast('请先写一些内容',true);return;}
  loading.style.display='block'; result.style.display='none';

  var prompts={
    expand:'原文片段：\n'+content.slice(-500)+'\n\n扩写要求：'+(extra||'请进行场景扩写，增加环境描写、人物动作和细节描写'),
    polish:'请润色以下段落，保持原意但让表达更流畅优美：\n'+content.slice(-1000)+(extra?'\n额外要求：'+extra:''),
    continue:'请根据以下内容自然续写下一段（500-1000字）：\n'+content.slice(-800)+(extra?'\n方向要求：'+extra:'')
  };

  API.post('/api/ai/generate',{provider:S.aiProvider,model:S.aiModel,action:action,prompt:prompts[action]})
    .then(function(data){
      loading.style.display='none';
      if(data.error){toast(data.error,true);return;}
      S._aiResult=data.content||'';
      document.getElementById('ai-result-text').textContent=S._aiResult;
      result.style.display='block';
    })
    .catch(function(e){ loading.style.display='none'; toast(e.message,true); });
}

function insertAIResult(){
  var ta=document.getElementById('et-content'); if(!ta||!S._aiResult)return;
  ta.value=ta.value+'\n\n'+S._aiResult;
  ta.dispatchEvent(new Event('input'));
  ta.focus(); ta.selectionStart=ta.selectionEnd=ta.value.length; ta.scrollTop=ta.scrollHeight;
  document.getElementById('ai-result-area').style.display='none';
  S._aiResult=''; toast('已插入');
}

// ========== WRITE PAGE ==========
function writePage(cb){
  API.get('/api/books').then(function(books){
    S.books=books;
    if(!books.length){
      cb('<div class="empty-state"><div style="font-size:48px;margin-bottom:12px">📝</div><h3>还没有作品</h3><p style="margin-top:8px">先去书库创建你的第一部作品吧</p></div><button class="btn btn-primary" onclick="nav(\'home\')" style="margin:0 auto;display:flex;width:80%">去创建作品</button>');
      return;
    }
    var html='<h3 style="font-size:16px;font-weight:600;margin-bottom:12px">📝 快速创作</h3>';
    books.forEach(function(b){
      html+='<div class="draft-item" onclick="quickWrite('+b.id+')">';
      html+='<div class="draft-cover">📖</div>';
      html+='<div class="draft-info"><h4>'+esc(b.title)+'</h4><div class="meta">'+(b.chapter_count||0)+'章 · '+fmt(b.word_count)+'字 · '+(b.status||'连载中')+'</div></div>';
      html+='<span style="color:var(--accent);font-size:24px">✏️</span></div>';
    });
    html+='<button class="btn btn-outline" onclick="nav(\'home\')" style="width:100%;margin-top:12px">📚 查看书库</button>';
    cb(html);
  }).catch(function(e){ cb('<div class="empty-state"><p>加载失败</p></div>'); });
}

function quickWrite(bookId){
  var b=S.books.find(function(x){return x.id===bookId;});
  if(!b)return;
  var count=(b.chapter_count||0)+1;
  showModal('新建章节 · '+esc(b.title),
    '<div class="form-group"><label class="form-label">章节标题</label><input class="form-input" id="qw-title" value="第'+count+'章" autofocus></div>',
    [{label:'取消'},{label:'开始写作',primary:true,onClick:function(){
      var t=document.getElementById('qw-title').value.trim()||'第'+count+'章';
      API.post('/api/books/'+bookId+'/chapters',{title:t})
        .then(function(r){ toast('已创建'); nav('editor/'+bookId+'/'+r.id); })
        .catch(function(e){ toast(e.message,true); });
    }}]
  );
}

// ========== AI PAGE ==========
function aiPage(cb){
  var b=S.currentBook;

  // Load providers from API
  API.get('/api/ai/providers').then(function(providers){
    S.aiProviders=providers;
    // Ensure current provider exists
    if(!providers.find(function(p){return p.id===S.aiProvider;})){
      S.aiProvider=providers.length?providers[0].id:'volcengine';
      S.aiModel=null;
    }
    renderAIPage(cb,b,providers);
  }).catch(function(){
    S.aiProviders=[];
    renderAIPage(cb,b,[]);
  });
}

function renderAIPage(cb,b,providers){
  var html='<div class="provider-selector">';
  providers.forEach(function(p){
    var active=S.aiProvider===p.id?' active':'';
    html+='<button class="provider-option'+active+'" onclick="setProvider(\''+p.id+'\')">'+p.name+'</button>';
  });
  if(!providers.length){
    html+='<span style="font-size:12px;color:var(--text-muted)">暂无可用提供商</span>';
  }
  html+='</div>';

  // Model selector for current provider
  var curr=providers.find(function(p){return p.id===S.aiProvider;});

  // File upload section
  html+='<div class="upload-section" style="margin:10px 0 14px;padding:10px;background:var(--surface);border-radius:8px;border:1px dashed var(--border)">';
  html+='<div style="font-size:13px;font-weight:500;margin-bottom:8px">📎 上传参考材料</div>';
  html+='<div style="display:flex;gap:6px;margin-bottom:6px">';
  html+='<label class="btn btn-small btn-outline" style="cursor:pointer;flex:1;text-align:center">📄 上传 Word 文档<input type="file" accept=".docx" style="display:none" onchange="handleFileUpload(this.files[0])"></label>';
  html+='<button class="btn btn-small btn-outline" style="flex:1" onclick="toggleFeishuInput()">🔗 飞书文档</button>';
  html+='</div>';
  html+='<div id="upload-info" style="font-size:11px;color:var(--text-muted)"></div>';
  html+='<div id="feishu-input-area" style="display:none;margin-top:6px">';
  html+='<input class="form-input" id="feishu-url" placeholder="粘贴飞书文档链接..." style="margin-bottom:6px;font-size:12px">';
  html+='<button class="btn btn-small btn-primary" onclick="readFeishuDoc()">读取文档</button>';
  html+='</div></div>';
  if(curr&&curr.models&&curr.models.length>1){
    html+='<div class="model-selector"><label class="form-label" style="font-size:12px">选择模型</label><select class="form-select" id="ai-model-select" onchange="saveProviderModel()">';
    curr.models.forEach(function(m){
      var sel=(S.aiModel||curr.model)===m?' selected':'';
      html+='<option value="'+m+'"'+sel+'>'+m+'</option>';
    });
    html+='</select></div>';
  }

  if(b) html+='<div style="font-size:13px;color:var(--text-secondary);margin-bottom:14px">📖 当前作品：<b>'+esc(b.title)+'</b> · '+(b.chapter_count||0)+'章 · '+fmt(b.word_count)+'字</div>';

  html+='<div class="char-grid" style="margin-bottom:16px">';
  var items=[
    {icon:'👤',label:'人物生成',sub:'角色设定',act:'characters'},
    {icon:'🌍',label:'世界观',sub:'世界观设定',act:'settings'},
    {icon:'📋',label:'大纲生成',sub:'剧情大纲',act:'outline'},
    {icon:'✏️',label:'场景扩写',sub:'段落扩写',act:'expand'}
  ];
  items.forEach(function(t){
    html+='<div class="char-grid-item" onclick="aiAction(\''+t.act+'\')"><div class="icon">'+t.icon+'</div><div class="label">'+t.label+'</div><div class="sub">'+t.sub+'</div></div>';
  });
  html+='</div>';

  html+='<div class="chat-container" id="ai-chat"></div>';
  html+='<div class="chat-input-bar">';
  html+='<input class="chat-input" id="ai-input" placeholder="输入创作需求，AI帮你完成..." onkeydown="if(event.key===\'Enter\')aiSend()">';
  html+='<button class="chat-send" onclick="aiSend()">➤</button></div>';
  html+='<div style="height:60px"></div>';
  html+='<div style="text-align:center;padding:8px 0;font-size:11px;color:var(--text-muted)">';
  html+='💡 在 <code>services/ai_providers.py</code> 中添加自定义提供商或模型';
  html+='</div>';
  cb(html);
}

function setProvider(p){ S.aiProvider=p; S.aiModel=null; render(); }

// File upload handlers
S.uploadedContent=null;

function handleFileUpload(file){
  if(!file) return;
  var info=document.getElementById('upload-info');
  if(info) info.innerHTML='⏳ 正在解析 '+esc(file.name)+'...';
  var form=new FormData();
  form.append('file',file);
  fetch('/api/upload/docx',{method:'POST',body:form})
    .then(function(r){return r.json();})
    .then(function(data){
      if(data.ok){
        S.uploadedContent=data.content;
        if(info) info.innerHTML='✅ '+esc(file.name)+' 已解析 ('+data.length+'字) <button class="btn btn-small btn-outline" onclick="clearUpload()">× 清除</button>';
        S.aiInfo=document.getElementById('feishu-input-area');
        if(S.aiInfo) S.aiInfo.style.display='none';
      } else {
        if(info) info.innerHTML='❌ '+(data.detail||'解析失败');
      }
    })
    .catch(function(e){
      if(info) info.innerHTML='❌ '+(e.message||'请求失败');
    });
}

var feishuVisible=false;
function toggleFeishuInput(){
  feishuVisible=!feishuVisible;
  var el=document.getElementById('feishu-input-area');
  if(el) el.style.display=feishuVisible?'block':'none';
}

function readFeishuDoc(){
  var url=document.getElementById('feishu-url');
  if(!url||!url.value.trim()) return;
  var info=document.getElementById('upload-info');
  if(info) info.innerHTML='⏳ 正在读取飞书文档...';
  fetch('/api/upload/feishu',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url:url.value.trim()})})
    .then(function(r){return r.json();})
    .then(function(data){
      if(data.ok){
        S.uploadedContent=data.content;
        if(info) info.innerHTML='✅ 飞书文档已读取 ('+data.length+'字) <button class="btn btn-small btn-outline" onclick="clearUpload()">× 清除</button>';
        url.value='';
        feishuVisible=false;
        var el=document.getElementById('feishu-input-area');
        if(el) el.style.display='none';
      } else {
        if(info) info.innerHTML='❌ '+(data.detail||'读取失败');
      }
    })
    .catch(function(e){
      if(info) info.innerHTML='❌ '+(e.message||'请求失败');
    });
}

function clearUpload(){
  S.uploadedContent=null;
  var info=document.getElementById('upload-info');
  if(info) info.innerHTML='';
}

function saveProviderModel(){
  var sel=document.getElementById('ai-model-select');
  if(sel) S.aiModel=sel.value||null;
}

function aiAction(act){
  var prompts={
    characters:'请为小说《'+(S.currentBook?S.currentBook.title:'新作品')+'》生成一个详细的人物设定，包含姓名、性别、年龄、角色定位、性格特征、背景故事、外貌、能力、动机等',
    settings:'请为小说生成世界观设定，包括时代背景、地理环境、社会结构、力量体系等',
    outline:'请为小说生成详细的故事大纲，分卷分章',
    expand:'请根据以下内容进行场景扩写\n'+(S.currentChapter&&S.currentChapter.content?S.currentChapter.content.slice(-500):'（请提供需要扩写的场景描述）')
  };
  S.aiChat.push({role:'user',text:prompts[act].slice(0,100)+'...'});
  doAIChat(act,uploadPrefix()+(prompts[act]||''));
}

function uploadPrefix(){
  if(S.uploadedContent) return '[参考材料]\n'+S.uploadedContent.slice(0,3000)+'\n\n---\n\n';
  return '';
}

function aiSend(){
  var inp=document.getElementById('ai-input'); if(!inp)return;
  var text=inp.value.trim(); if(!text)return;
  inp.value=''; S.aiChat.push({role:'user',text:text});
  renderAIChat();
  doAIChat('chat',text);
}

function doAIChat(action,prompt){
  S.aiChat.push({role:'ai',text:'...',loading:true});
  renderAIChat();
  var body={provider:S.aiProvider,action:action,prompt:prompt};
  if(S.aiModel) body.model=S.aiModel;
  API.post('/api/ai/generate',body)
    .then(function(data){
      S.aiChat.pop();
      if(data.error){ S.aiChat.push({role:'ai',text:'❌ '+data.error,error:true}); }
      else S.aiChat.push({role:'ai',text:data.content||'',action:action});
      renderAIChat();
    })
    .catch(function(e){
      S.aiChat.pop();
      S.aiChat.push({role:'ai',text:'❌ '+e.message,error:true});
      renderAIChat();
    });
}

function renderAIChat(){
  var el=document.getElementById('ai-chat'); if(!el)return;
  el.innerHTML=S.aiChat.map(function(m,i){
    if(m.loading) return '<div class="chat-bubble ai"><span style="color:var(--accent)">⏳ AI 思考中...</span></div>';
    var cls=m.role==='user'?'user':'ai';
    var actions='';
    if(m.role==='ai'&&!m.error){
      actions='<div style="margin-top:8px;display:flex;gap:6px;font-size:11px">';
      actions+='<button class="btn btn-small btn-outline" onclick="copyText('+i+')">📋 复制</button>';
      if(m.action==='characters') actions+='<button class="btn btn-small btn-primary" onclick="saveAIChatChar('+i+')">💾 保存角色</button>';
      if(m.action==='settings') actions+='<button class="btn btn-small btn-primary" onclick="saveAIChatSetting('+i+')">💾 保存设定</button>';
      if(m.action==='outline') actions+='<button class="btn btn-small btn-outline" onclick="copyText('+i+')">📋 复制大纲</button>';
      actions+='</div>';
    }
    return '<div class="chat-bubble '+cls+'"><div style="white-space:pre-wrap">'+esc(m.text)+'</div>'+actions+'</div>';
  }).join('');
  setTimeout(function(){ el.scrollTop=el.scrollHeight; },100);
}

function copyText(idx){
  var t=S.aiChat[idx]?.text||'';
  navigator.clipboard.writeText(t).then(function(){ toast('已复制'); });
}

function saveAIChatChar(idx){
  if(!S.currentBook){toast('请先选择一部作品',true);return;}
  var text=S.aiChat[idx]?.text||'';
  var nameMatch=text.match(/姓名[：:]\s*(.+)/);
  var name=nameMatch?nameMatch[1].trim():'AI生成角色';
  API.post('/api/books/'+S.currentBook.id+'/characters',{name:name,notes:text})
    .then(function(){ toast('角色「'+name+'」已保存'); })
    .catch(function(e){ toast(e.message,true); });
}

function saveAIChatSetting(idx){
  if(!S.currentBook){toast('请先选择一部作品',true);return;}
  var text=S.aiChat[idx]?.text||'';
  API.post('/api/books/'+S.currentBook.id+'/settings',{category:'general',title:'AI生成设定',content:text})
    .then(function(){ toast('设定已保存'); })
    .catch(function(e){ toast(e.message,true); });
}

function attachAI(){
  renderAIChat();
  var inp=document.getElementById('ai-input'); if(inp)inp.focus();
}

// ========== LOGIN PAGE ==========
function loginPage(cb){
  var html='<div class="auth-page" style="padding:40px 20px;text-align:center">';
  html+='<div style="font-size:64px;margin-bottom:12px">📖</div>';
  html+='<h2 style="margin-bottom:4px">墨韵小说写作器</h2>';
  html+='<p style="color:var(--text-muted);margin-bottom:24px;font-size:13px">登录后开始创作</p>';
  html+='<div class="form-group"><label class="form-label">用户名</label>';
  html+='<input class="form-input" id="login-user" placeholder="输入用户名" autofocus></div>';
  html+='<div class="form-group"><label class="form-label">密码</label>';
  html+='<input class="form-input" id="login-pass" type="password" placeholder="输入密码" onkeydown="if(event.key===\'Enter\')doLogin()"></div>';
  html+='<button class="btn btn-primary" onclick="doLogin()" style="width:100%;margin-top:8px">登 录</button>';
  html+='<div id="register-link" style="margin-top:16px;font-size:13px"></div>';
  html+='<div id="login-msg" style="margin-top:12px;font-size:13px;color:var(--error)"></div></div>';
  cb(html);
  // Check if registration is open
  fetch('/api/auth/settings').then(function(r){return r.json();}).then(function(d){
    var el=document.getElementById('register-link');
    if(el&&d.registration_open!==false){
      el.innerHTML='<span style="color:var(--text-muted)">还没有账号？</span> <a href="#register" style="color:var(--accent)">注册</a>';
    }
  }).catch(function(){});
}

function doLogin(){
  var user=document.getElementById('login-user');
  var pass=document.getElementById('login-pass');
  var msg=document.getElementById('login-msg');
  if(!user||!pass) return;
  if(!user.value.trim()||!pass.value.trim()){
    if(msg) msg.textContent='请填写用户名和密码';
    return;
  }
  fetch('/api/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:user.value.trim(),password:pass.value})})
    .then(function(r){ return r.json().then(function(d){ d.status=r.status; return d; }); })
    .then(function(d){
      if(d.token){
        S.token=d.token; S.user=d.user;
        localStorage.setItem('moyun-token',d.token);
        if(msg) msg.textContent='';
        render();
      } else {
        if(msg) msg.textContent=d.detail||'登录失败';
      }
    })
    .catch(function(e){
      if(msg) msg.textContent='请求失败: '+e.message;
    });
}

// ========== REGISTER PAGE ==========
function registerPage(cb){
  var html='<div class="auth-page" style="padding:40px 20px;text-align:center">';
  html+='<div style="font-size:48px;margin-bottom:12px">✍️</div>';
  html+='<h2 style="margin-bottom:4px">注册账号</h2>';
  html+='<p style="color:var(--text-muted);margin-bottom:24px;font-size:13px">注册后等待管理员审批</p>';
  html+='<div class="form-group"><label class="form-label">用户名</label>';
  html+='<input class="form-input" id="reg-user" placeholder="至少2个字符" autofocus></div>';
  html+='<div class="form-group"><label class="form-label">密码</label>';
  html+='<input class="form-input" id="reg-pass" type="password" placeholder="至少4个字符"></div>';
  html+='<div class="form-group"><label class="form-label">确认密码</label>';
  html+='<input class="form-input" id="reg-confirm" type="password" placeholder="再次输入密码" onkeydown="if(event.key===\'Enter\')doRegister()"></div>';
  html+='<button class="btn btn-primary" onclick="doRegister()" style="width:100%;margin-top:8px">注 册</button>';
  html+='<div id="reg-msg" style="margin-top:12px;font-size:13px;color:var(--error)"></div>';
  html+='<div style="margin-top:16px;font-size:13px"><a href="#login" style="color:var(--accent)">← 返回登录</a></div></div>';
  cb(html);
}

function doRegister(){
  var user=document.getElementById('reg-user');
  var pass=document.getElementById('reg-pass');
  var confirm=document.getElementById('reg-confirm');
  var msg=document.getElementById('reg-msg');
  if(!user||!pass||!confirm) return;
  if(!user.value.trim()||!pass.value.trim()){
    if(msg) msg.textContent='请填写所有字段';
    return;
  }
  if(pass.value!==confirm.value){
    if(msg) msg.textContent='两次密码不一致';
    return;
  }
  fetch('/api/auth/register',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:user.value.trim(),password:pass.value})})
    .then(function(r){ return r.json().then(function(d){ d.status=r.status; return d; }); })
    .then(function(d){
      if(d.ok){
        if(msg){ msg.style.color='var(--success, #27ae60)'; msg.textContent='✅ 注册成功！请等待管理员审核'; }
        user.value=''; pass.value=''; confirm.value='';
      } else {
        if(msg) msg.textContent=d.detail||'注册失败';
      }
    })
    .catch(function(e){
      if(msg) msg.textContent='请求失败: '+e.message;
    });
}

// ========== ME PAGE ==========
function mePage(cb){
  API.get('/api/books').then(function(books){
    S.books=books;
    var totalWords=books.reduce(function(s,b){return s+(b.word_count||0);},0);
    var totalChs=books.reduce(function(s,b){return s+(b.chapter_count||0);},0);
    var username=S.user?S.user.username:'用户';
    var role=S.user&&S.user.role==='admin'?'管理员':'普通用户';

    var html='<div class="profile-header">';
    html+='<div class="profile-avatar">'+username.charAt(0).toUpperCase()+'</div>';
    html+='<h3 style="font-size:18px;font-weight:600">'+esc(username)+'</h3>';
    html+='<span style="font-size:12px;color:var(--text-muted)">'+role+' · 小说写作助手</span></div>';

    html+='<div class="stats-grid">';
    html+='<div class="stats-card"><div class="stats-value">'+books.length+'</div><div class="stats-label">作品数</div></div>';
    html+='<div class="stats-card"><div class="stats-value">'+fmt(totalWords)+'</div><div class="stats-label">总字数</div></div>';
    html+='<div class="stats-card"><div class="stats-value">'+totalChs+'</div><div class="stats-label">总章节</div></div>';
    html+='</div>';

    // Admin panel
    if(S.user&&S.user.role==='admin'){
      html+='<div class="admin-section" style="margin:16px 0;padding:14px;background:var(--surface);border-radius:8px;border:1px solid var(--border)">';
      html+='<h4 style="font-size:14px;font-weight:600;margin-bottom:12px">⚙️ 管理员控制</h4>';
      html+='<div id="admin-pending" style="margin-bottom:12px">加载中...</div>';
      html+='<div class="theme-toggle" style="margin-top:8px"><span class="label">🔓 开放注册</span>';
      html+='<button class="toggle-switch" id="reg-toggle" onclick="toggleRegistration()"></button></div>';
      html+='</div>';
    }

    html+='<div class="theme-toggle"><span class="label">🌓 深色模式</span><button class="toggle-switch" id="theme-switch" onclick="toggleTheme()"></button></div>';

    var menus=[
      {icon:'📚',text:'我的作品',f:function(){nav('home');}},
      {icon:'🤖',text:'AI助手设置',f:function(){nav('ai');}},
      {icon:'✍️',text:'写作偏好',f:function(){toast('即将上线');}},
      {icon:'👁️',text:'阅读设置',f:function(){toast('即将上线');}},
      {icon:'📊',text:'数据统计',f:function(){toast('即将上线');}},
      {icon:'💬',text:'帮助与反馈',f:function(){toast('即将上线');}},
      {icon:'ℹ️',text:'关于墨韵',f:function(){toast('墨韵小说写作器 v1.0\nAI-powered · 火山引擎 & DeepSeek\nMade with ❤️');}}
    ];
    html+='<div class="menu-list">';
    menus.forEach(function(m,i){
      html+='<div class="menu-item" id="menu-'+i+'"><span style="font-size:20px">'+m.icon+'</span><span class="text">'+m.text+'</span><span class="chevron">›</span></div>';
    });
    html+='</div>';
    html+='<div style="text-align:center;padding:16px 0">';
    html+='<button class="btn btn-secondary" onclick="logout()" style="width:100%">🚪 退出登录</button>';
    html+='</div>';
    html+='<div style="text-align:center;padding:10px 0 20px"><span style="font-size:11px;color:var(--text-muted)">墨韵 · 让写作成为一种享受</span></div>';
    cb(html);

    setTimeout(function(){
      menus.forEach(function(m,i){
        var el=document.getElementById('menu-'+i);
        if(el)el.onclick=m.f;
      });
      if(S.user&&S.user.role==='admin') loadAdminPanel();
    },0);
  }).catch(function(e){ cb('<div class="empty-state"><p>加载失败</p></div>'); });
}

function loadAdminPanel(){
  var el=document.getElementById('admin-pending');
  if(!el) return;
  fetch('/api/auth/settings').then(function(r){return r.json();}).then(function(s){
    var toggle=document.getElementById('reg-toggle');
    if(toggle) toggle.classList.toggle('active',s.registration_open!==false);
  }).catch(function(){});
  fetch('/api/auth/pending',{headers:{'Authorization':'Bearer '+S.token}})
    .then(function(r){return r.json();})
    .then(function(list){
      if(!list||!list.length){ el.innerHTML='<span style="font-size:12px;color:var(--text-muted)">✅ 暂无待审批用户</span>'; return; }
      var h='<div style="font-size:12px;font-weight:500;margin-bottom:6px">待审批用户 ('+list.length+')</div>';
      list.forEach(function(u){
        h+='<div style="display:flex;justify-content:space-between;align-items:center;padding:6px 0;border-bottom:1px solid var(--border)">';
        h+='<span>'+esc(u.username)+'</span>';
        h+='<span><button class="btn btn-small btn-primary" onclick="approveUser('+u.id+',\'approve\')">通过</button> ';
        h+='<button class="btn btn-small btn-secondary" onclick="approveUser('+u.id+',\'reject\')">拒绝</button></span>';
        h+='</div>';
      });
      el.innerHTML=h;
    })
    .catch(function(){ el.innerHTML='<span style="font-size:12px;color:var(--error)">加载失败</span>'; });
}

function approveUser(userId,action){
  fetch('/api/auth/approve/'+userId,{method:'POST',headers:{'Content-Type':'application/json','Authorization':'Bearer '+S.token},body:JSON.stringify({action:action})})
    .then(function(r){return r.json();})
    .then(function(d){
      if(d.ok){ toast(action==='approve'?'✅ 已通过审批':'已拒绝'); loadAdminPanel(); }
      else toast('操作失败',true);
    })
    .catch(function(){ toast('请求失败',true); });
}

function toggleRegistration(){
  var toggle=document.getElementById('reg-toggle');
  var newVal=!toggle.classList.contains('active');
  fetch('/api/auth/settings',{method:'POST',headers:{'Content-Type':'application/json','Authorization':'Bearer '+S.token},body:JSON.stringify({registration_open:newVal})})
    .then(function(r){return r.json();})
    .then(function(d){
      if(d.ok){ toggle.classList.toggle('active',newVal); toast(newVal?'注册已开放':'注册已关闭'); }
    })
    .catch(function(){ toast('操作失败',true); });
}

// ========== APP INIT ==========
window.addEventListener('hashchange',render);
window.addEventListener('load',function(){
  applyTheme();
  API.get('/api/books').then(function(b){S.books=b;}).catch(function(){});
  render();
});

// Export to window
window.nav=nav;
window.toggleTheme=toggleTheme;
window.filterBooks=filterBooks;
window.createBook=createBook;
window.startReading=startReading;
window.addChapter=addChapter;
window.addVolume=addVolume;
window.editBook=editBook;
window.deleteBookConfirm=deleteBookConfirm;
window.quickWrite=quickWrite;
window.saveNow=saveNow;
window.insertMark=insertMark;
window.toggleAIPanel=toggleAIPanel;
window.editorAI=editorAI;
window.insertAIResult=insertAIResult;
window.setProvider=setProvider;
window.aiAction=aiAction;
window.aiSend=aiSend;
window.copyText=copyText;
window.saveAIChatChar=saveAIChatChar;
window.saveAIChatSetting=saveAIChatSetting;
window.save=save;
window.saveProviderModel=saveProviderModel;
window.doLogin=doLogin;
window.doRegister=doRegister;
window.approveUser=approveUser;
window.toggleRegistration=toggleRegistration;
window.logout=logout;
window.handleFileUpload=handleFileUpload;
window.toggleFeishuInput=toggleFeishuInput;
window.readFeishuDoc=readFeishuDoc;
window.clearUpload=clearUpload;
window.uploadPrefix=uploadPrefix;

})();
