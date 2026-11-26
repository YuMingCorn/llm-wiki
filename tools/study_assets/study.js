(function(){
  var key='wiki-study:'+location.pathname;
  var boxes=[].slice.call(document.querySelectorAll('ol.quiz textarea'));
  var saved={};
  try{saved=JSON.parse(localStorage.getItem(key)||'{}')}catch(e){}
  if(!saved||typeof saved!=='object'||Array.isArray(saved))saved={};
  boxes.forEach(function(t,i){
    if(saved[i])t.value=saved[i];
    t.addEventListener('input',function(){
      saved[i]=t.value;
      try{localStorage.setItem(key,JSON.stringify(saved))}catch(e){}
    });
  });
  var btn=document.getElementById('copy');
  if(btn)btn.addEventListener('click',function(){
    var stem=decodeURIComponent(location.pathname.split('/').pop()||'').replace(/\.html$/,'');
    var lines=['wiki-study 作答：'+(document.body.dataset.title||'')+'（'+stem+'）',''];
    [].forEach.call(document.querySelectorAll('ol.quiz li'),function(li,i){
      var q=li.querySelector('.q').textContent.trim();
      var a=(boxes[i]&&boxes[i].value.trim())||'（沒答）';
      lines.push('Q'+li.dataset.n+'〔'+li.dataset.cat+'〕'+q,'答：'+a,'');
    });
    var text=lines.join('\n');
    var status=document.getElementById('copied');
    var done=function(){status.textContent='已複製，貼回對話即可';};
    var manual=function(){
      var box=document.getElementById('copy-manual');
      if(!box){
        box=document.createElement('textarea');box.id='copy-manual';box.rows=8;
        btn.parentNode.parentNode.insertBefore(box,btn.parentNode.nextSibling);
      }
      box.value=text;box.focus();box.select();
      status.textContent='沒能自動複製：下面的文字已選取，請按 Ctrl+C';
    };
    var fallback=function(){
      var ta=document.createElement('textarea');ta.value=text;
      ta.style.position='fixed';ta.style.top='0';ta.style.opacity='0';
      document.body.appendChild(ta);ta.select();
      var ok=false;
      try{ok=document.execCommand('copy')}catch(e){}
      document.body.removeChild(ta);
      if(ok)done();else manual();
    };
    if(navigator.clipboard&&navigator.clipboard.writeText)
      navigator.clipboard.writeText(text).then(done,fallback);
    else fallback();
  });
  var askBox=document.getElementById('ask-box');
  if(askBox){
    var askKey='wiki-study-ask:'+location.pathname;
    try{askBox.value=localStorage.getItem(askKey)||''}catch(e){}
    askBox.addEventListener('input',function(){
      try{localStorage.setItem(askKey,askBox.value)}catch(e){}
    });
    var askBtn=document.getElementById('copy-ask');
    var askStatus=document.getElementById('copied-ask');
    if(askBtn)askBtn.addEventListener('click',function(){
      var qs=askBox.value.split('\n').map(function(s){return s.trim()}).filter(Boolean);
      if(!qs.length){askStatus.textContent='還沒有問題';return;}
      var stem=decodeURIComponent(location.pathname.split('/').pop()||'').replace(/\.html$/,'');
      var text=['wiki-study 提問：'+(document.body.dataset.title||'')+'（'+stem+'）',''
        ].concat(qs.map(function(q,i){return (i+1)+'. '+q})).join('\n');
      var done=function(){askStatus.textContent='已複製，貼回對話即可';};
      var manual=function(){
        askBox.select();askStatus.textContent='沒能自動複製：請按 Ctrl+C 複製問題';
      };
      var fallback=function(){
        var ta=document.createElement('textarea');ta.value=text;
        ta.style.position='fixed';ta.style.top='0';ta.style.opacity='0';
        document.body.appendChild(ta);ta.select();
        var ok=false;
        try{ok=document.execCommand('copy')}catch(e){}
        document.body.removeChild(ta);
        if(ok)done();else manual();
      };
      if(navigator.clipboard&&navigator.clipboard.writeText)
        navigator.clipboard.writeText(text).then(done,fallback);
      else fallback();
    });
  }
  function drawDiagram(){
    var m=document.querySelector('pre.mermaid');
    if(!m||!window.mermaid)return;
    var dark=window.matchMedia&&matchMedia('(prefers-color-scheme: dark)').matches;
    try{
      mermaid.initialize({startOnLoad:false,theme:dark?'dark':'neutral',
        fontFamily:'Helvetica, "Microsoft JhengHei", sans-serif',securityLevel:'strict'});
      m.hidden=false;
      mermaid.run({nodes:[m]}).then(function(){
        var t=document.querySelector('pre.tree');if(t)t.hidden=true;
      },function(){m.hidden=true;});
    }catch(e){m.hidden=true;}
  }
  if(document.readyState==='complete')drawDiagram();
  else window.addEventListener('load',drawDiagram);
})();
