/* ===== 墨韵 · 小说写作器 — SPA Client ===== */
(function(){
'use strict';

// ========== API ==========
var API = {
  get: function(p) { return fetch(p).then(function(r){ if(!r.ok)throw Error('HTTP '+r.status); return r.json(); }); },
  post: function(p,d) { return fetch(p,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(d)}).then(function(r){ if(!r.ok)throw Error('HTTP '+r.status); return r.json(); }); },
  put: function(p,d) { return fetch(p,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(d)}).then(function(r){ if(!r.ok)throw Error('HTTP '+r.status); return r.json(); }); },
  del: function(p) { return fetch(p,{method:'DELETE'}).then(function(r){ if(!r.ok)throw Error('HTTP '+r.status); return r.json(); }); }
};

// ========== State ==========
var S = {
  theme: localStorage.getItem('moyun-theme')||'light',
  books: [],
  currentBook: null,
  currentChapter: null,
  aiProvider: 'volcengine',
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

  API.post('/api/ai/generate',{provider:S.aiProvider,action:action,prompt:prompts[action]})
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
  var html='<div class="provider-selector">';
  html+='<button class="provider-option'+(S.aiProvider==='volcengine'?' active':'')+'" onclick="setProvider(\'volcengine\')">🌋 火山引擎</button>';
  html+='<button class="provider-option'+(S.aiProvider==='deepseek'?' active':'')+'" onclick="setProvider(\'deepseek\')">🤖 DeepSeek</button>';
  html+='</div>';
  if(b) html+='<div style="font-size:13px;color:var(--text-secondary);margin-bottom:14px">📖 当前作品：<b>'+esc(b.title)+'</b> · '+(b.chapter_count||0)+'章 · '+fmt(b.word_count)+'字</div>';

  html+='<div class="char-grid" style="margin-bottom:16px">';
  var items=[{icon:'👤',label:'人物生成',sub:'角色设定',act:'characters'},{icon:'🌍',label:'世界观',sub:'世界观设定',act:'settings'},{icon:'📋',label:'大纲生成',sub:'剧情大纲',act:'outline'},{icon:'✏️',label:'场景扩写',sub:'段落扩写',act:'expand'}];
  items.forEach(function(t){
    html+='<div class="char-grid-item" onclick="aiAction(\''+t.act+'\')"><div class="icon">'+t.icon+'</div><div class="label">'+t.label+'</div><div class="sub">'+t.sub+'</div></div>';
  });
  html+='</div>';

  html+='<div class="chat-container" id="ai-chat"></div>';
  html+='<div class="chat-input-bar">';
  html+='<input class="chat-input" id="ai-input" placeholder="输入创作需求，AI帮你完成..." onkeydown="if(event.key===\'Enter\')aiSend()">';
  html+='<button class="chat-send" onclick="aiSend()">➤</button></div>';
  html+='<div style="height:60px"></div>';
  cb(html);
}

function setProvider(p){ S.aiProvider=p; render(); }

function aiAction(act){
  var prompts={
    characters:'请为小说《'+(S.currentBook?S.currentBook.title:'新作品')+'》生成一个详细的人物设定，包含姓名、性别、年龄、角色定位、性格特征、背景故事、外貌、能力、动机等',
    settings:'请为小说生成世界观设定，包括时代背景、地理环境、社会结构、力量体系等',
    outline:'请为小说生成详细的故事大纲，分卷分章',
    expand:'请根据以下内容进行场景扩写\n'+(S.currentChapter&&S.currentChapter.content?S.currentChapter.content.slice(-500):'（请提供需要扩写的场景描述）')
  };
  S.aiChat.push({role:'user',text:prompts[act].slice(0,100)+'...'});
  doAIChat(act,prompts[act]);
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
  API.post('/api/ai/generate',{provider:S.aiProvider,action:action,prompt:prompt})
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

// ========== ME PAGE ==========
function mePage(cb){
  API.get('/api/books').then(function(books){
    S.books=books;
    var totalWords=books.reduce(function(s,b){return s+(b.word_count||0);},0);
    var totalChs=books.reduce(function(s,b){return s+(b.chapter_count||0);},0);

    var html='<div class="profile-header">';
    html+='<div class="profile-avatar">墨</div>';
    html+='<h3 style="font-size:18px;font-weight:600">墨韵用户</h3>';
    html+='<span style="font-size:12px;color:var(--text-muted)">小说写作助手 v1.0</span></div>';

    html+='<div class="stats-grid">';
    html+='<div class="stats-card"><div class="stats-value">'+books.length+'</div><div class="stats-label">作品数</div></div>';
    html+='<div class="stats-card"><div class="stats-value">'+fmt(totalWords)+'</div><div class="stats-label">总字数</div></div>';
    html+='<div class="stats-card"><div class="stats-value">'+totalChs+'</div><div class="stats-label">总章节</div></div>';
    html+='</div>';

    html+='<div class="theme-toggle"><span class="label">🌓 深色模式</span><button class="toggle-switch" id="theme-switch" onclick="toggleTheme()"></button></div>';

    var menus=[
      {icon:'📚',text:'我的作品',f:function(){nav('home');}},
      {icon:'🤖',text:'AI助手设置',f:function(){nav('ai');}},
      {icon:'✍️',text:'写作偏好',f:function(){toast('即将上线');}},
      {icon:'👁️',text:'阅读设置',f:function(){toast('即将上线');}},
      {icon:'📊',text:'数据统计',f:function(){toast('即将上线');}},
      {icon:'💬',text:'帮助与反馈',f:function(){toast('即将上线');}},
      {icon:'ℹ️',text:'关于墨韵',f:function(){toast('墨韵小说写作器 v1.0\\nAI-powered · 火山引擎 & DeepSeek\\nMade with ❤️');}}
    ];
    html+='<div class="menu-list">';
    menus.forEach(function(m,i){
      html+='<div class="menu-item" id="menu-'+i+'"><span style="font-size:20px">'+m.icon+'</span><span class="text">'+m.text+'</span><span class="chevron">›</span></div>';
    });
    html+='</div>';
    html+='<div style="text-align:center;padding:20px 0"><span style="font-size:11px;color:var(--text-muted)">墨韵 · 让写作成为一种享受</span></div>';
    cb(html);

    // Bind menu clicks after DOM update
    setTimeout(function(){
      menus.forEach(function(m,i){
        var el=document.getElementById('menu-'+i);
        if(el)el.onclick=m.f;
      });
    },0);
  }).catch(function(e){ cb('<div class="empty-state"><p>加载失败</p></div>'); });
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

})();
