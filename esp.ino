/*
  Paste Link  -  Seeed Studio XIAO ESP32-C3
  Phone -> WiFi -> ESP32 -> Bluetooth -> Laptop        (type on laptop)
  Laptop script -> WiFi -> ESP32 -> Phone page         (selected text + commands)
  Phone page -> Gemini or Claude (your key, stored on the phone only) -> reply
  -> typed on the laptop through the ESP32.

  Laptop script commands (POST /p):
    t=...      set the "From laptop" text
    clear=1    empty the "From laptop" box
    ask=1      phone asks the AI now                (G key)
    stop=1     stop typing / cancel the AI request  (F9 key)
    newchat=1  phone forgets the conversation       (M key)

  The page is served as 3 small files: /  /core.js  /ai.js

  Needs: HijelHID_BLEKeyboard + NimBLE-Arduino (2.3.8 or newer)
  Board: XIAO_ESP32C3, USB CDC On Boot: Enabled
*/

#include <WiFi.h>
#include <WebServer.h>
#include <ESPmDNS.h>
#include <HTTPClient.h>
#include <WiFiClientSecure.h>
#include <HijelHID_BLEKeyboard.h>

// ---------- Settings ----------
const char* WIFI_SSID = "IOT";
const char* WIFI_PASS = "123456789";
const char* HOST_NAME = "paste";          // http://paste.local
const char* BT_NAME   = "Paste Link";     // name shown in laptop Bluetooth

// ---------- IP registry (backend/ folder, hosted on Render) ----------
const char* DEVICE_NAME  = "saurav esp ip";                     // change this for each new ESP32
const char* BACKEND_URL  = "https://YOUR-APP.onrender.com";     // your Render URL, no trailing /
const char* DEVICE_TOKEN = "change-me-to-a-long-secret";        // same as DEVICE_TOKEN on Render
const unsigned long REPORT_EVERY_MS = 5UL * 60 * 1000;          // re-report every 5 min
const unsigned long REPORT_RETRY_MS = 30UL * 1000;              // retry after a failure

const uint8_t STATIC_HOST = 150;          // fixed address <subnet>.150, 0 = plain DHCP

const bool   CLEAR_BONDS = false;         // true for ONE flash to forget old pairings
const size_t MAX_CHUNK   = 2048;          // max characters per typing request
const size_t MAX_RX      = 20000;         // max characters accepted from the laptop
const unsigned long STOP_HOLD_MS = 1500;  // after a stop, typing requests are refused this long

HijelHID_BLEKeyboard keyboard(BT_NAME, "ESP32", 100);
WebServer server(80);

// Text received from the laptop script
String        latestText;
unsigned long latestId = 0;

// Commands from the laptop script, kept in a small ring so none are lost
const int     CMD_SLOTS = 8;
String        cmdName[CMD_SLOTS];
unsigned long cmdIdOf[CMD_SLOTS];
unsigned long cmdId = 0;

unsigned long stopAt = 0;                 // millis() of the last stop, 0 = none
unsigned long lastTypeAt = 0;             // millis() of the last typing request
unsigned long nextReportAt = 0;
IPAddress     reportedIp;

// =====================================================================
//  FILE 1: the page (HTML + CSS)
// =====================================================================
const char PAGE[] PROGMEM = R"HTML(
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#000000">
<title>Paste Link</title>
<script>
window.onerror=function(m,s,l){
  var e=document.getElementById('st');
  if(e){e.className='';e.textContent='JS error: '+m+' (line '+l+')';}
};
</script>
<style>
  *{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
  body{margin:0;background:#000;color:#fff;font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;
       padding:max(16px,env(safe-area-inset-top)) 16px 32px}
  .wrap{max-width:560px;margin:0 auto}
  header{display:flex;align-items:center;justify-content:space-between;margin-bottom:16px;gap:10px}
  h1{font-size:20px;margin:0;font-weight:600}
  h2{font-size:15px;margin:30px 0 10px;font-weight:600;color:#ccc;display:flex;justify-content:space-between;align-items:center}
  h2 small{font-weight:400;color:#888;font-size:12px}
  #st{font-size:13px;color:#888;text-align:right}
  #st.ok{color:#2ecc71}
  textarea,input[type=text],input[type=password],select{
    width:100%;font-size:16px;line-height:1.45;padding:12px;border-radius:10px;border:1px solid #333;
    background:#000;color:#fff;outline:none;font-family:inherit}
  textarea{height:200px;resize:vertical}
  textarea:focus,input:focus,select:focus{border-color:#fff}
  #rx{height:170px}
  #ans{height:200px}
  #sys{height:110px}
  .meta{display:flex;justify-content:space-between;font-size:12px;color:#888;margin:8px 0 18px}
  .lbl{font-size:13px;color:#888;margin:12px 0 6px}
  input[type=range]{width:100%;accent-color:#fff;margin:6px 0}
  .sw{display:flex;align-items:center;gap:10px;margin:12px 0;font-size:14px}
  .sw input{width:20px;height:20px;accent-color:#fff;flex:none}
  button{width:100%;padding:15px;border-radius:10px;font-size:16px;font-weight:600;font-family:inherit;margin-top:10px}
  .pri{background:#fff;color:#000;border:0}
  .pri:disabled{opacity:.5}
  #stop{display:none;background:#e74c3c;color:#fff;border:0}
  .sec{background:#000;color:#fff;border:1px solid #333}
  .two{display:grid;grid-template-columns:1fr 1fr;gap:10px}
  .bar{height:4px;background:#222;border-radius:4px;overflow:hidden;margin-top:14px;display:none}
  .bar i{display:block;height:100%;width:0;background:#fff}
  .msg{margin-top:14px;font-size:14px;min-height:20px;text-align:center;color:#aaa}
  .good{color:#2ecc71!important}.warn{color:#f1c40f!important}.err{color:#e74c3c!important}
  details{border:1px solid #333;border-radius:10px;padding:4px 12px;margin-top:12px}
  summary{padding:10px 0;font-size:14px;color:#ccc;cursor:pointer}
  .hide{display:none}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>Paste Link</h1>
    <div id="st">Checking...</div>
  </header>

  <textarea id="t" placeholder="Type or paste text here" autocapitalize="off" autocomplete="off" spellcheck="false"></textarea>
  <div class="meta"><span id="cnt">0 characters</span><span id="eta"></span></div>

  <div class="lbl">Delay per key: <span id="sv">8</span> ms (lower = faster)</div>
  <input type="range" id="sp" min="1" max="60" value="8">

  <label class="sw"><input type="checkbox" id="en"> Press Enter at the end</label>

  <button class="pri" id="go">Type on laptop</button>
  <button id="stop">STOP</button>
  <div class="bar" id="bar"><i id="fill"></i></div>
  <button class="sec" id="clr">Clear</button>
  <div class="msg" id="msg"></div>

  <h2>From laptop</h2>
  <div class="lbl">Select text on the laptop, then X = add, G = ask AI, M = new chat, F9 = stop.</div>
  <textarea id="rx" readonly placeholder="Selected text appears here"></textarea>
  <div class="msg" id="rmsg"></div>

  <h2>AI <small id="chatinfo">New chat</small></h2>
  <details id="settings">
    <summary>Settings</summary>
    <div class="lbl">Provider</div>
    <select id="prov">
      <option value="gemini">Gemini (Google)</option>
      <option value="claude">Claude (Anthropic)</option>
    </select>

    <div id="gbox">
      <div class="lbl">Gemini API key (saved on this phone only)</div>
      <input type="password" id="gkey" placeholder="Paste your Gemini API key" autocomplete="off">
      <div class="lbl">Gemini model</div>
      <input type="text" id="gmodel" autocapitalize="off" autocomplete="off" spellcheck="false">
    </div>

    <div id="cbox" class="hide">
      <div class="lbl">Claude API key (saved on this phone only)</div>
      <input type="password" id="ckey" placeholder="sk-ant-..." autocomplete="off">
      <div class="lbl">Claude model (claude-opus-5-5, claude-sonnet-5-5, claude-haiku-5-5)</div>
      <input type="text" id="cmodel" autocapitalize="off" autocomplete="off" spellcheck="false">
      <div class="lbl">Effort (lower = faster and cheaper)</div>
      <select id="ceff">
        <option value="low">low</option>
        <option value="medium">medium</option>
        <option value="high">high</option>
      </select>
    </div>

    <div class="lbl">Instructions for the AI</div>
    <textarea id="sys" autocapitalize="off" spellcheck="false"></textarea>
    <label class="sw"><input type="checkbox" id="mem" checked> Remember the conversation (follow-up questions)</label>
  </details>

  <label class="sw"><input type="checkbox" id="aa"> Ask automatically 3 s after new text arrives</label>
  <label class="sw"><input type="checkbox" id="at" checked> Type the reply on the laptop automatically</label>
  <label class="sw"><input type="checkbox" id="cl" checked> Clean the reply (no markdown, ASCII only)</label>
  <div class="lbl">Wait before typing: <span id="wv">4</span> s (time to click where the answer should go)</div>
  <input type="range" id="wait" min="0" max="15" value="4">

  <div class="two">
    <button class="pri" id="ask">Ask AI</button>
    <button class="sec" id="again">Type reply again</button>
  </div>
  <button class="sec" id="newchat">New chat</button>
  <textarea id="ans" placeholder="The reply appears here" style="margin-top:10px"></textarea>
  <div class="msg" id="gmsg"></div>
</div>

<script src="/core.js?v=4"></script>
<script src="/ai.js?v=4"></script>
<script>
setTimeout(function(){
  if(!window.coreLoaded){
    var e=document.getElementById('st');
    if(e && e.textContent==='Checking...') e.textContent='Script did not load - reload the page';
  }
},5000);
</script>
</body>
</html>
)HTML";

// =====================================================================
//  FILE 2: /core.js  (typing, status, laptop box, laptop commands)
// =====================================================================
const char JS_CORE[] PROGMEM = R"JS(
window.coreLoaded=true;
var $=function(id){return document.getElementById(id);};
var typing=false, stopReq=false, wake=null, lastId=0, lastCmd=-1;

function sleep(ms){return new Promise(function(r){setTimeout(r,ms);});}
function say(m,c){var e=$('msg');e.textContent=m;e.className='msg '+(c||'');}
function rsay(m,c){var e=$('rmsg');e.textContent=m;e.className='msg '+(c||'');}
function fmt(s){return s<60?s+' s':s<3600?Math.round(s/60)+' min':(s/3600).toFixed(1)+' h';}

function info(){
  var n=$('t').value.length;
  $('cnt').textContent=n.toLocaleString()+' characters';
  $('eta').textContent=n?('about '+fmt(Math.ceil(n*2*$('sp').value/1000))):'';
}
$('sp').oninput=function(){$('sv').textContent=$('sp').value;info();};
$('t').oninput=info;
$('clr').onclick=function(){if(typing)return;$('t').value='';info();say('');};

async function status(){
  if(typing) return;
  try{
    var j=await (await fetch('/status')).json();
    $('st').className=j.ble?'ok':'';
    $('st').textContent=j.ble?'Laptop connected':'Not paired';
    if(!j.ble) say('Pair "'+j.name+'" in the laptop Bluetooth settings.','warn');
    else if($('msg').className.indexOf('warn')>=0) say('');
  }catch(e){
    $('st').className='';$('st').textContent='Unreachable';
  }
}
setInterval(status,3000);status();

async function keepAwake(){
  try{ if('wakeLock' in navigator && !wake) wake=await navigator.wakeLock.request('screen'); }catch(e){}
}
document.addEventListener('visibilitychange',function(){
  if(document.visibilityState==='visible'){wake=null;keepAwake();}
});
document.addEventListener('click',keepAwake,{once:true});
keepAwake();

function ui(on){
  typing=on;
  $('go').style.display=on?'none':'block';
  $('stop').style.display=on?'block':'none';
  $('bar').style.display=on?'block':'none';
  $('t').readOnly=on;
  if(on)$('stop').textContent='STOP';
}

function requestStop(){
  stopReq=true;
  $('stop').textContent='Stopping...';
  if(window.onStop) window.onStop();
}
$('stop').onclick=function(){
  requestStop();
  fetch('/stop',{method:'POST'}).catch(function(){});
};

// Types text on the laptop in small chunks. waitSec = countdown before typing starts.
async function typeOnLaptop(text,enter,waitSec){
  if(typing||!text) return false;
  var delay=parseInt($('sp').value);
  var chunk=Math.max(40,Math.min(1500,Math.round(700/(2*delay))));
  var total=text.length, pos=0, ok=true, errMsg='';

  stopReq=false;ui(true);$('fill').style.width='0%';
  await keepAwake();

  for(var s=waitSec||0;s>0&&!stopReq;s--){
    say('Typing in '+s+' s - click where the text should go...','warn');
    await sleep(1000);
  }

  if(!stopReq) say('Typing... keep the cursor in the target window.');
  var t0=Date.now();

  while(pos<total && !stopReq){
    var part=text.slice(pos,pos+chunk);
    var last=pos+part.length>=total;
    var body=new URLSearchParams({text:part,delay:delay,enter:(last&&enter)?1:0});
    try{
      var r=await fetch('/type',{method:'POST',body:body});
      var m=await r.text();
      if(m.indexOf('Stopped')===0){stopReq=true;break;}
      if(m.indexOf('Done')!==0){ok=false;errMsg=m;break;}
    }catch(e){ok=false;errMsg='Lost connection to the ESP32.';break;}
    pos+=part.length;
    $('fill').style.width=(pos*100/total)+'%';
    var left=Math.ceil((total-pos)*2*delay/1000);
    say('Typed '+pos.toLocaleString()+' / '+total.toLocaleString()+(left>1?'  -  '+fmt(left)+' left':''));
  }

  ui(false);
  if(!ok) say(errMsg+' Stopped at character '+pos.toLocaleString()+' of '+total.toLocaleString()+'.','err');
  else if(stopReq) say('Stopped at character '+pos.toLocaleString()+' of '+total.toLocaleString()+'.','warn');
  else say('Done. Typed '+total.toLocaleString()+' characters in '+fmt(Math.round((Date.now()-t0)/1000))+'.','good');
  return ok&&!stopReq;
}

$('go').onclick=function(){
  var text=$('t').value;
  if(!text){say('Nothing to type.','warn');return;}
  typeOnLaptop(text,$('en').checked,0);
};

// Laptop -> phone text. force=true fetches it even if nothing changed.
async function pollRx(force){
  if(typing && !force) return;
  try{
    var r=await fetch('/latest?since='+(force?0:lastId));
    if(r.status===200){
      var txt=await r.text();
      var id=parseInt(r.headers.get('X-Id'))||lastId;
      var changed=id!==lastId;
      $('rx').value=txt;
      $('rx').scrollTop=$('rx').scrollHeight;
      lastId=id;
      if(changed){
        rsay(txt?('Updated '+new Date().toLocaleTimeString()):'Cleared.',txt?'good':'warn');
        if(txt && !force && window.onNewText) window.onNewText(txt);
      }
    }
  }catch(e){}
}
window.syncRx=function(){return pollRx(true);};
setInterval(function(){pollRx(false);},700);pollRx(false);

// Laptop commands (ask / stop / newchat). Runs even while typing so F9 works.
var cmdBusy=false;
async function pollCmd(){
  if(cmdBusy) return;
  cmdBusy=true;
  try{
    var r=await fetch('/cmd?since='+Math.max(lastCmd,0));
    var id=parseInt(r.headers.get('X-Id'));
    if(lastCmd<0){ lastCmd=isNaN(id)?0:id; }      // first load: skip old commands
    else if(r.status===200){
      var lines=(await r.text()).split('\n');
      for(var i=0;i<lines.length;i++){
        var p=lines[i].split(' ');
        var cid=parseInt(p[0]), name=p[1];
        if(!cid || cid<=lastCmd) continue;
        lastCmd=cid;
        if(name==='stop'){ if(typing) requestStop(); else if(window.onStop) window.onStop(); }
        else if(window.onCmd) window.onCmd(name);
      }
    }
  }catch(e){}
  cmdBusy=false;
}
setInterval(pollCmd,400);pollCmd();
info();
)JS";

// =====================================================================
//  FILE 3: /ai.js  (Gemini + Claude; separate, so a problem here cannot break typing)
// =====================================================================
const char JS_AI[] PROGMEM = R"JS(
(function(){
try{
  var G=function(id){return document.getElementById(id);};
  function store(k,v){try{localStorage.setItem(k,v);}catch(e){}}
  function load(k,d){try{var v=localStorage.getItem(k);return v===null?d:v;}catch(e){return d;}}
  function gsay(m,c){var e=G('gmsg');e.textContent=m;e.className='msg '+(c||'');}
  var working=false, askTimer=null, ctrl=null;
  var MAX_TURNS=40;

  // ----- settings -----
  var fields={prov:'gemini',gkey:'',gmodel:'gemini-2.5-flash',ckey:'',cmodel:'claude-opus-5-5',ceff:'low',
    sys:'Answer concisely in plain text only. Use only standard ASCII characters. No markdown, no bullet symbols, no code fences. If the task asks for code, output only the code.'};
  Object.keys(fields).forEach(function(k){
    G(k).value=load(k==='sys'?'gsys':k,fields[k]);
    G(k).oninput=G(k).onchange=function(){
      store(k==='sys'?'gsys':k,(k==='sys'||k==='prov'||k==='ceff')?G(k).value:G(k).value.trim());
      if(k==='prov') showProv();
    };
  });
  var checks={aa:'0',at:'1',cl:'1',mem:'1'};
  Object.keys(checks).forEach(function(k){
    G(k).checked=load(k,checks[k])==='1';
    G(k).onchange=function(){store(k,G(k).checked?'1':'0');};
  });
  G('wait').value=load('wait','4');G('wv').textContent=G('wait').value;
  G('wait').oninput=function(){G('wv').textContent=G('wait').value;store('wait',G('wait').value);};

  function showProv(){
    var c=G('prov').value==='claude';
    G('gbox').className=c?'hide':'';
    G('cbox').className=c?'':'hide';
  }
  showProv();
  function keyMissing(){return !(G('prov').value==='claude'?G('ckey'):G('gkey')).value.trim();}
  if(keyMissing()) G('settings').open=true;

  // ----- chat memory: [{role:'user'|'assistant', text, raw?}] -----
  var hist=[];
  try{hist=JSON.parse(load('hist','[]'))||[];}catch(e){hist=[];}
  function saveHist(){
    if(hist.length>MAX_TURNS) hist=hist.slice(hist.length-MAX_TURNS);
    while(hist.length && hist[0].role!=='user') hist.shift();
    store('hist',JSON.stringify(hist));
    var n=Math.floor(hist.length/2);
    G('chatinfo').textContent=n?('Chat: '+n+' question'+(n>1?'s':'')):'New chat';
  }
  saveHist();
  function newChat(){
    hist=[];saveHist();G('ans').value='';
    gsay('New chat started. The AI forgot the earlier questions.','good');
  }
  G('newchat').onclick=newChat;

  // ----- providers -----
  async function readJson(r){var j={};try{j=await r.json();}catch(e){}return j;}

  async function askGemini(turns,sys,signal){
    var key=G('gkey').value.trim();
    var model=G('gmodel').value.trim()||'gemini-2.5-flash';
    if(!key) throw new Error('Open Settings and paste your Gemini API key.');
    var body={contents:turns.map(function(t){
      return {role:t.role==='assistant'?'model':'user',parts:[{text:t.text}]};
    })};
    if(sys) body.systemInstruction={parts:[{text:sys}]};
    var r=await fetch('https://generativelanguage.googleapis.com/v1beta/models/'+encodeURIComponent(model)+':generateContent',{
      method:'POST',signal:signal,
      headers:{'Content-Type':'application/json','x-goog-api-key':key},
      body:JSON.stringify(body)
    });
    var j=await readJson(r);
    if(!r.ok) throw new Error((j.error&&j.error.message)||('Gemini error '+r.status));
    var c=j.candidates&&j.candidates[0];
    var parts=c&&c.content&&c.content.parts;
    var text=parts?parts.map(function(p){return p.text||'';}).join(''):'';
    if(!text) throw new Error('Gemini returned no text'+(j.promptFeedback&&j.promptFeedback.blockReason?' (blocked: '+j.promptFeedback.blockReason+')':'.'));
    return {text:text};
  }

  async function askClaude(turns,sys,signal){
    var key=G('ckey').value.trim();
    var model=G('cmodel').value.trim()||'claude-opus-5-5';
    if(!key) throw new Error('Open Settings and paste your Claude API key.');
    var body={
      model:model,
      max_tokens:16000,
      output_config:{effort:G('ceff').value||'low'},
      messages:turns.map(function(t){
        return {role:t.role,content:(t.role==='assistant'&&t.raw)?t.raw:t.text};
      })
    };
    if(sys) body.system=sys;
    var headers={
      'content-type':'application/json',
      'x-api-key':key,
      'anthropic-version':'2026-10-21',
      'anthropic-dangerous-direct-browser-access':'true'
    };
    // If this model declines a request, let Anthropic retry it on another model automatically.
    if(/^claude-(opus-5|sonnet-5-5|fable-5)/.test(model)){
      body.fallbacks='default';
      headers['anthropic-beta']='server-side-fallback-2026-07-01';
    }
    var r=await fetch('https://api.anthropic.com/v1/messages',{
      method:'POST',signal:signal,headers:headers,body:JSON.stringify(body)
    });
    var j=await readJson(r);
    if(!r.ok) throw new Error((j.error&&j.error.message)||('Claude error '+r.status));
    if(j.stop_reason==='refusal'){
      var why=j.stop_details&&j.stop_details.explanation;
      throw new Error('Claude declined this request'+(why?': '+why:'.'));
    }
    var blocks=j.content||[];
    var text=blocks.filter(function(b){return b.type==='text';}).map(function(b){return b.text;}).join('');
    if(!text) throw new Error('Claude returned no text.');
    if(j.stop_reason==='max_tokens') text+='\n[reply cut off]';
    return {text:text,raw:blocks};
  }

  function clean(s){
    return s
      .replace(/```[a-zA-Z0-9_+-]*\n?/g,'')
      .replace(/\*\*/g,'')
      .replace(/^#+\s*/gm,'')
      .replace(/[‘’]/g,"'").replace(/[“”]/g,'"')
      .replace(/[–—]/g,'-').replace(/…/g,'...').replace(/ /g,' ')
      .replace(/•/g,'-')
      .replace(/[^\x09\x0A\x0D\x20-\x7E]/g,'')
      .trim();
  }

  // ----- ask -----
  async function runAsk(fromLaptop){
    if(working||typing) return;
    clearTimeout(askTimer);
    working=true;G('ask').disabled=true;
    try{
      if(fromLaptop && window.syncRx) await window.syncRx();   // make sure the newest text is here
      var q=G('rx').value.trim();
      if(!q){gsay('Nothing to ask yet. Select text on the laptop and press X or G.','warn');return;}

      var prov=G('prov').value;
      var name=prov==='claude'?'Claude':'Gemini';
      var turns=(G('mem').checked?hist.slice():[]).concat([{role:'user',text:q}]);
      ctrl=('AbortController' in window)?new AbortController():null;
      gsay('Asking '+name+(turns.length>1?' (with '+Math.floor(turns.length/2)+' earlier questions)':'')+'...');

      var res=await (prov==='claude'?askClaude:askGemini)(turns,G('sys').value.trim(),ctrl&&ctrl.signal);
      ctrl=null;

      hist.push({role:'user',text:q});
      hist.push({role:'assistant',text:res.text,raw:res.raw});
      saveHist();

      var a=G('cl').checked?clean(res.text):res.text;
      G('ans').value=a;
      gsay(name+' replied ('+a.length.toLocaleString()+' characters).','good');
      if(G('at').checked){
        working=false;G('ask').disabled=false;
        await typeOnLaptop(a,G('en').checked,parseInt(G('wait').value)||0);
      }
    }catch(e){
      if(e&&e.name==='AbortError') gsay('Cancelled.','warn');
      else gsay((e&&e.message)||'Request failed.','err');
    }finally{
      ctrl=null;working=false;G('ask').disabled=false;
    }
  }
  G('ask').onclick=function(){runAsk(false);};
  G('again').onclick=function(){
    var a=G('ans').value;
    if(!a){gsay('No reply to type yet.','warn');return;}
    typeOnLaptop(a,G('en').checked,parseInt(G('wait').value)||0);
  };

  // ----- hooks called by core.js -----
  window.onNewText=function(){
    if(G('aa').checked){
      clearTimeout(askTimer);
      gsay('New text. Asking in 3 s (more text restarts the timer)...','warn');
      askTimer=setTimeout(function(){runAsk(false);},3000);
    }
  };
  window.onStop=function(){
    clearTimeout(askTimer);
    if(ctrl){ctrl.abort();}
  };
  window.onCmd=function(name){
    if(name==='ask') runAsk(true);
    else if(name==='newchat'){ if(ctrl) ctrl.abort(); clearTimeout(askTimer); newChat(); }
  };
}catch(e){
  var m=document.getElementById('gmsg');
  if(m){m.textContent='AI script error: '+e.message;m.className='msg err';}
}
})();
)JS";

// ---------- Typing ----------
bool stoppedRecently() {
  return stopAt != 0 && millis() - stopAt < STOP_HOLD_MS;
}

void typeText(const String& text, int delayMs, bool pressEnter) {
  keyboard.setTapDelay(delayMs);   // how long each key is held
  keyboard.setKeyGap(delayMs);     // gap after each key

  for (size_t i = 0; i < text.length(); i++) {
    if (!keyboard.isPaired()) break;
    char c = text[i];
    if (c == '\r') continue;
    if (c == '\n') keyboard.tap(KEY_RETURN);
    else if (c == '\t') keyboard.tap(KEY_TAB);
    else if ((uint8_t)c >= 32 && (uint8_t)c < 127) keyboard.write((uint8_t)c);
    // non-ASCII is skipped: HID cannot type Unicode
    yield();
  }
  if (pressEnter && keyboard.isPaired()) keyboard.tap(KEY_RETURN);
  keyboard.releaseAll();
}

void pushCmd(const char* name) {
  cmdId++;
  int slot = cmdId % CMD_SLOTS;
  cmdName[slot] = name;
  cmdIdOf[slot] = cmdId;
  Serial.printf("Command %lu: %s\n", cmdId, name);
}

// ---------- Handlers ----------
void handleRoot() {
  server.sendHeader("Cache-Control", "no-store");
  server.send_P(200, "text/html", PAGE);
}

void handleCoreJs() {
  server.sendHeader("Cache-Control", "no-store");
  server.send_P(200, "application/javascript", JS_CORE);
}

void handleAiJs() {
  server.sendHeader("Cache-Control", "no-store");
  server.send_P(200, "application/javascript", JS_AI);
}

void handleStatus() {
  String j = String("{\"ble\":") + (keyboard.isPaired() ? "true" : "false") +
             ",\"name\":\"" + BT_NAME + "\"}";
  server.send(200, "application/json", j);
}

// Called once per chunk by the phone page
void handleType() {
  if (stoppedRecently()) {
    server.send(200, "text/plain", "Stopped.");
    return;
  }
  if (!keyboard.isPaired()) {
    server.send(200, "text/plain", "Laptop not connected over Bluetooth.");
    return;
  }
  String text = server.arg("text");
  if (text.length() == 0) {
    server.send(200, "text/plain", "Done. Nothing to type.");
    return;
  }
  if (text.length() > MAX_CHUNK) {
    server.send(200, "text/plain", "Chunk too large.");
    return;
  }

  int  delayMs = constrain(server.arg("delay").toInt(), 1, 200);
  bool enter   = server.arg("enter") == "1";

  lastTypeAt = millis();
  typeText(text, delayMs, enter);
  lastTypeAt = millis();
  server.send(200, "text/plain", "Done. Typed " + String(text.length()) + " characters.");
}

void handleStop() {
  keyboard.releaseAll();           // make sure no key is left held
  server.send(200, "text/plain", "Stopped.");
}

// The laptop script posts here: t=... | clear=1 | ask=1 | stop=1 | newchat=1
void handlePush() {
  if (server.arg("stop") == "1") {
    keyboard.releaseAll();
    stopAt = millis();
    if (stopAt == 0) stopAt = 1;
    pushCmd("stop");
    server.send(200, "text/plain", "ok");
    return;
  }
  if (server.arg("ask") == "1") {
    pushCmd("ask");
    server.send(200, "text/plain", "ok");
    return;
  }
  if (server.arg("newchat") == "1") {
    pushCmd("newchat");
    server.send(200, "text/plain", "ok");
    return;
  }
  if (server.arg("clear") == "1") {
    latestText = "";
    latestId++;
    Serial.println("Cleared by laptop.");
    server.send(200, "text/plain", "ok");
    return;
  }
  if (!server.hasArg("t")) {
    server.send(400, "text/plain", "Unknown request.");
    return;
  }
  String text = server.arg("t");
  if (text.length() == 0) {
    server.send(400, "text/plain", "Empty.");
    return;
  }
  if (text.length() > MAX_RX) text = text.substring(0, MAX_RX);
  latestText = text;
  latestId++;
  Serial.printf("Got %u chars from laptop (id %lu)\n", (unsigned)text.length(), latestId);
  server.send(200, "text/plain", "ok");
}

// The phone page polls this. 204 = nothing new.
void handleLatest() {
  unsigned long since = strtoul(server.arg("since").c_str(), nullptr, 10);
  server.sendHeader("X-Id", String(latestId));
  if (latestId == 0 || latestId == since) {
    server.send(204, "text/plain", "");
    return;
  }
  server.send(200, "text/plain; charset=utf-8", latestText);
}

// The phone page polls this for laptop commands. Body: one "id name" per line.
void handleCmd() {
  unsigned long since = strtoul(server.arg("since").c_str(), nullptr, 10);
  server.sendHeader("X-Id", String(cmdId));
  if (cmdId == 0 || since >= cmdId) {
    server.send(204, "text/plain", "");
    return;
  }
  String out;
  unsigned long first = (cmdId > CMD_SLOTS) ? cmdId - CMD_SLOTS + 1 : 1;
  if (since + 1 > first) first = since + 1;
  for (unsigned long id = first; id <= cmdId; id++) {
    int slot = id % CMD_SLOTS;
    if (cmdIdOf[slot] == id) out += String(id) + " " + cmdName[slot] + "\n";
  }
  server.send(200, "text/plain", out);
}

// ---------- IP registry ----------
String jsonEsc(const String& s) {
  String o;
  for (size_t i = 0; i < s.length(); i++) {
    char c = s[i];
    if (c == '"' || c == '\\') { o += '\\'; o += c; }
    else if ((uint8_t)c < 32) o += ' ';
    else o += c;
  }
  return o;
}

bool reportIp() {
  String base = BACKEND_URL;
  if (base.indexOf("YOUR-APP") >= 0) return true;        // not set up yet: skip quietly
  while (base.endsWith("/")) base.remove(base.length() - 1);

  WiFiClientSecure tls;
  tls.setInsecure();                 // no certificate check: only a LAN IP is sent
  HTTPClient http;
  http.setConnectTimeout(8000);
  http.setTimeout(10000);            // Render's free plan can take a while to wake up
  if (!http.begin(tls, base + "/api/report")) {
    Serial.println("Registry: bad BACKEND_URL");
    return false;
  }
  http.addHeader("Content-Type", "application/json");
  http.addHeader("Authorization", String("Bearer ") + DEVICE_TOKEN);

  String body = String("{\"name\":\"") + jsonEsc(DEVICE_NAME) +
                "\",\"ip\":\"" + WiFi.localIP().toString() +
                "\",\"ssid\":\"" + jsonEsc(WiFi.SSID()) +
                "\",\"rssi\":" + String(WiFi.RSSI()) +
                ",\"mac\":\"" + WiFi.macAddress() +
                "\",\"ble\":" + (keyboard.isPaired() ? "true" : "false") +
                ",\"uptime\":" + String(millis() / 1000) + "}";

  int code = http.POST(body);
  http.end();
  Serial.printf("Registry: sent %s as \"%s\" -> %d\n",
                WiFi.localIP().toString().c_str(), DEVICE_NAME, code);
  return code == 200;
}

void maybeReport() {
  unsigned long now = millis();
  if (WiFi.localIP() != reportedIp) {          // just connected, or the address changed
    reportedIp = WiFi.localIP();
    nextReportAt = now;
  }
  if ((long)(now - nextReportAt) < 0) return;
  if (lastTypeAt && now - lastTypeAt < 10000) return;   // don't pause in the middle of typing

  nextReportAt = now + (reportIp() ? REPORT_EVERY_MS : REPORT_RETRY_MS);
}

// ---------- WiFi with fixed address ----------
bool waitWiFi(unsigned long ms) {
  unsigned long t0 = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - t0 < ms) {
    delay(250);
    Serial.print(".");
  }
  Serial.println();
  return WiFi.status() == WL_CONNECTED;
}

void connectWiFi() {
  WiFi.mode(WIFI_STA);
  WiFi.setHostname(HOST_NAME);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  Serial.print("Connecting to WiFi");
  if (!waitWiFi(20000)) {
    Serial.println("WiFi failed - check the hotspot. Retrying in the background.");
    return;
  }

  if (STATIC_HOST > 0) {
    IPAddress gw   = WiFi.gatewayIP();
    IPAddress mask = WiFi.subnetMask();
    IPAddress fixedIp(gw[0], gw[1], gw[2], STATIC_HOST);   // assumes a /24 subnet

    Serial.printf("Switching to fixed address %s\n", fixedIp.toString().c_str());
    WiFi.disconnect();
    delay(200);
    WiFi.config(fixedIp, gw, mask, gw);
    WiFi.begin(WIFI_SSID, WIFI_PASS);
    Serial.print("Reconnecting");
    if (!waitWiFi(15000)) {
      Serial.println("Fixed address failed - falling back to DHCP.");
      WiFi.disconnect();
      WiFi.config(IPAddress(), IPAddress(), IPAddress());   // back to DHCP
      WiFi.begin(WIFI_SSID, WIFI_PASS);
      waitWiFi(15000);
    }
  }

  if (WiFi.status() == WL_CONNECTED) {
    Serial.print("Open on phone: http://");
    Serial.println(WiFi.localIP());
    if (MDNS.begin(HOST_NAME)) {
      Serial.printf("Also try: http://%s.local\n", HOST_NAME);
    }
  }
}

// ---------- Setup / loop ----------
void setup() {
  Serial.begin(115200);
  delay(2000);
  Serial.println();
  Serial.printf("Boot. Free heap: %u bytes\n", ESP.getFreeHeap());

  keyboard.setLogLevel(HIDLogLevel::Normal);
  keyboard.begin();
  Serial.printf("Bluetooth ready as \"%s\". Free heap: %u bytes\n", BT_NAME, ESP.getFreeHeap());

  if (CLEAR_BONDS) {               // must come AFTER begin()
    keyboard.clearBonds();
    Serial.println("Bonds cleared - re-pair from the laptop.");
  }

  connectWiFi();

  server.on("/",         HTTP_GET,  handleRoot);
  server.on("/core.js",  HTTP_GET,  handleCoreJs);
  server.on("/ai.js",    HTTP_GET,  handleAiJs);
  server.on("/status",   HTTP_GET,  handleStatus);
  server.on("/type",     HTTP_POST, handleType);
  server.on("/stop",     HTTP_POST, handleStop);
  server.on("/p",        HTTP_POST, handlePush);
  server.on("/latest",   HTTP_GET,  handleLatest);
  server.on("/cmd",      HTTP_GET,  handleCmd);
  server.begin();
}

void loop() {
  static unsigned long lastRetry = 0;

  if (WiFi.status() != WL_CONNECTED) {
    if (millis() - lastRetry > 5000) {
      lastRetry = millis();
      WiFi.disconnect();
      WiFi.begin(WIFI_SSID, WIFI_PASS);
      Serial.println("WiFi dropped - reconnecting...");
    }
    return;
  }
  server.handleClient();
  maybeReport();
}
