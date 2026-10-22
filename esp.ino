

/*
  Paste Link  -  Seeed Studio XIAO ESP32-C3
  Phone -> WiFi -> ESP32 -> Bluetooth -> Laptop        (type on laptop)
  Laptop script -> WiFi -> ESP32 -> Phone page         (selected text, automatic)
  Phone page -> Gemini API (your key, stored on the phone only) -> reply
  -> typed on the laptop through the ESP32.

  The page is served as 3 small files: /  /core.js  /gemini.js

  Needs: HijelHID_BLEKeyboard + NimBLE-Arduino (2.3.8 or newer)
  Board: XIAO_ESP32C3, USB CDC On Boot: Enabled
*/

#include <WiFi.h>
#include <WebServer.h>
#include <ESPmDNS.h>
#include <HijelHID_BLEKeyboard.h>

// ---------- Settings ----------
const char* WIFI_SSID = "IOT";
const char* WIFI_PASS = "123456789";
const char* HOST_NAME = "paste";          // http://paste.local
const char* BT_NAME   = "Paste Link";     // name shown in laptop Bluetooth

const uint8_t STATIC_HOST = 150;          // fixed address <subnet>.150, 0 = plain DHCP

const bool   CLEAR_BONDS = false;         // true for ONE flash to forget old pairings
const size_t MAX_CHUNK   = 2048;          // max characters per typing request
const size_t MAX_RX      = 20000;         // max characters accepted from the laptop

HijelHID_BLEKeyboard keyboard(BT_NAME, "ESP32", 100);
WebServer server(80);

// Text received from the laptop script
String        latestText;
unsigned long latestId = 0;

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
  h2{font-size:15px;margin:30px 0 10px;font-weight:600;color:#ccc}
  #st{font-size:13px;color:#888;text-align:right}
  #st.ok{color:#2ecc71}
  textarea,input[type=text],input[type=password]{
    width:100%;font-size:16px;line-height:1.45;padding:12px;border-radius:10px;border:1px solid #333;
    background:#000;color:#fff;outline:none;font-family:inherit}
  textarea{height:200px;resize:vertical}
  textarea:focus,input:focus{border-color:#fff}
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
  <div class="lbl">Select text on the laptop and press X. It is added here.</div>
  <textarea id="rx" readonly placeholder="Selected text appears here"></textarea>
  <div class="msg" id="rmsg"></div>

  <h2>Gemini</h2>
  <details id="settings">
    <summary>Settings</summary>
    <div class="lbl">API key (saved on this phone only)</div>
    <input type="password" id="gkey" placeholder="Paste your Gemini API key" autocomplete="off">
    <div class="lbl">Model (change it if you get a "model not found" error)</div>
    <input type="text" id="gmodel" autocapitalize="off" autocomplete="off" spellcheck="false">
    <div class="lbl">Instructions for Gemini</div>
    <textarea id="sys" autocapitalize="off" spellcheck="false"></textarea>
  </details>

  <label class="sw"><input type="checkbox" id="aa"> Ask Gemini automatically 3 s after new text arrives</label>
  <label class="sw"><input type="checkbox" id="at" checked> Type the reply on the laptop automatically</label>
  <label class="sw"><input type="checkbox" id="cl" checked> Clean the reply (no markdown, ASCII only)</label>
  <div class="lbl">Wait before typing: <span id="wv">4</span> s (time to click where the answer should go)</div>
  <input type="range" id="wait" min="0" max="15" value="4">

  <div class="two">
    <button class="pri" id="ask">Ask Gemini</button>
    <button class="sec" id="again">Type reply again</button>
  </div>
  <textarea id="ans" placeholder="Gemini's reply appears here" style="margin-top:10px"></textarea>
  <div class="msg" id="gmsg"></div>
</div>

<script src="/core.js?v=3"></script>
<script src="/gemini.js?v=3"></script>
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
//  FILE 2: /core.js  (typing, status, laptop box; no Gemini code)
// =====================================================================
const char JS_CORE[] PROGMEM = R"JS(
window.coreLoaded=true;
var $=function(id){return document.getElementById(id);};
var typing=false, stopReq=false, wake=null, lastId=0;

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

$('stop').onclick=function(){
  stopReq=true;
  $('stop').textContent='Stopping...';
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

// Laptop -> phone (automatic)
async function pollRx(){
  if(typing) return;
  try{
    var r=await fetch('/latest?since='+lastId);
    if(r.status===200){
      var txt=await r.text();
      $('rx').value=txt;
      $('rx').scrollTop=$('rx').scrollHeight;
      lastId=parseInt(r.headers.get('X-Id'))||lastId;
      rsay(txt?('Updated '+new Date().toLocaleTimeString()):'Cleared.',txt?'good':'warn');
      if(txt && window.onNewText) window.onNewText(txt);
    }
  }catch(e){}
}
setInterval(pollRx,700);pollRx();
info();
)JS";

// =====================================================================
//  FILE 3: /gemini.js  (separate, so a problem here cannot break typing)
// =====================================================================
const char JS_GEMINI[] PROGMEM = R"JS(
(function(){
try{
  var G=function(id){return document.getElementById(id);};
  function store(k,v){try{localStorage.setItem(k,v);}catch(e){}}
  function load(k,d){try{var v=localStorage.getItem(k);return v===null?d:v;}catch(e){return d;}}
  function gsay(m,c){var e=G('gmsg');e.textContent=m;e.className='msg '+(c||'');}
  var working=false, askTimer=null;

  G('gkey').value=load('gkey','');
  G('gmodel').value=load('gmodel','gemini-2.5-flash');
  G('sys').value=load('gsys','Answer concisely in plain text only. Use only standard ASCII characters. No markdown, no bullet symbols, no code fences. If the task asks for code, output only the code.');
  G('aa').checked=load('aa','0')==='1';
  G('at').checked=load('at','1')==='1';
  G('cl').checked=load('cl','1')==='1';
  G('wait').value=load('wait','4');G('wv').textContent=G('wait').value;
  G('gkey').oninput=function(){store('gkey',G('gkey').value.trim());};
  G('gmodel').oninput=function(){store('gmodel',G('gmodel').value.trim());};
  G('sys').oninput=function(){store('gsys',G('sys').value);};
  G('aa').onchange=function(){store('aa',G('aa').checked?'1':'0');};
  G('at').onchange=function(){store('at',G('at').checked?'1':'0');};
  G('cl').onchange=function(){store('cl',G('cl').checked?'1':'0');};
  G('wait').oninput=function(){G('wv').textContent=G('wait').value;store('wait',G('wait').value);};
  if(!G('gkey').value) G('settings').open=true;

  async function askGemini(prompt){
    var key=G('gkey').value.trim();
    var model=G('gmodel').value.trim()||'gemini-2.5-flash';
    if(!key) throw new Error('Open Settings and paste your Gemini API key.');
    var body={contents:[{role:'user',parts:[{text:prompt}]}]};
    var sys=G('sys').value.trim();
    if(sys) body.systemInstruction={parts:[{text:sys}]};
    var r=await fetch('https://generativelanguage.googleapis.com/v1beta/models/'+encodeURIComponent(model)+':generateContent',{
      method:'POST',
      headers:{'Content-Type':'application/json','x-goog-api-key':key},
      body:JSON.stringify(body)
    });
    var j={};
    try{j=await r.json();}catch(e){}
    if(!r.ok) throw new Error((j.error&&j.error.message)||('Gemini error '+r.status));
    var c=j.candidates&&j.candidates[0];
    var parts=c&&c.content&&c.content.parts;
    var text=parts?parts.map(function(p){return p.text||'';}).join(''):'';
    if(!text) throw new Error('Gemini returned no text'+(j.promptFeedback&&j.promptFeedback.blockReason?' (blocked: '+j.promptFeedback.blockReason+')':'.'));
    return text;
  }

  function clean(s){
    return s
      .replace(/```[a-zA-Z0-9_+-]*\n?/g,'')
      .replace(/\*\*/g,'')
      .replace(/^#+\s*/gm,'')
      .replace(/[\u2018\u2019]/g,"'").replace(/[\u201C\u201D]/g,'"')
      .replace(/[\u2013\u2014]/g,'-').replace(/\u2026/g,'...').replace(/\u00A0/g,' ')
      .replace(/[^\x09\x0A\x0D\x20-\x7E]/g,'')
      .trim();
  }

  async function runAsk(){
    if(working||typing) return;
    var q=G('rx').value.trim();
    if(!q){gsay('Nothing to ask yet. Select text on the laptop and press X.','warn');return;}
    working=true;G('ask').disabled=true;
    gsay('Asking Gemini...');
    try{
      var a=await askGemini(q);
      if(G('cl').checked) a=clean(a);
      G('ans').value=a;
      gsay('Reply received ('+a.length.toLocaleString()+' characters).','good');
      if(G('at').checked) await typeOnLaptop(a,G('en').checked,parseInt(G('wait').value)||0);
    }catch(e){
      gsay(e.message||'Request failed.','err');
    }
    working=false;G('ask').disabled=false;
  }
  G('ask').onclick=runAsk;
  G('again').onclick=function(){
    var a=G('ans').value;
    if(!a){gsay('No reply to type yet.','warn');return;}
    typeOnLaptop(a,G('en').checked,parseInt(G('wait').value)||0);
  };

  // called by core.js whenever new text arrives from the laptop
  window.onNewText=function(){
    if(G('aa').checked){
      clearTimeout(askTimer);
      gsay('New text. Asking Gemini in 3 s (more text restarts the timer)...','warn');
      askTimer=setTimeout(runAsk,3000);
    }
  };
}catch(e){
  var m=document.getElementById('gmsg');
  if(m){m.textContent='Gemini script error: '+e.message;m.className='msg err';}
}
})();
)JS";

// ---------- Typing ----------
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

// ---------- Handlers ----------
void handleRoot() {
  server.sendHeader("Cache-Control", "no-store");
  server.send_P(200, "text/html", PAGE);
}

void handleCoreJs() {
  server.sendHeader("Cache-Control", "no-store");
  server.send_P(200, "application/javascript", JS_CORE);
}

void handleGeminiJs() {
  server.sendHeader("Cache-Control", "no-store");
  server.send_P(200, "application/javascript", JS_GEMINI);
}

void handleStatus() {
  String j = String("{\"ble\":") + (keyboard.isPaired() ? "true" : "false") +
             ",\"name\":\"" + BT_NAME + "\"}";
  server.send(200, "application/json", j);
}

// Called once per chunk by the phone page
void handleType() {
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

  typeText(text, delayMs, enter);
  server.send(200, "text/plain", "Done. Typed " + String(text.length()) + " characters.");
}

void handleStop() {
  keyboard.releaseAll();           // make sure no key is left held
  server.send(200, "text/plain", "Stopped.");
}

// The laptop script posts selected text here (t=...) or empties the box (clear=1)
void handlePush() {
  if (server.arg("clear") == "1") {
    latestText = "";
    latestId++;
    Serial.println("Cleared by laptop.");
    server.send(200, "text/plain", "ok");
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

  server.on("/",           HTTP_GET,  handleRoot);
  server.on("/core.js",    HTTP_GET,  handleCoreJs);
  server.on("/gemini.js",  HTTP_GET,  handleGeminiJs);
  server.on("/status",     HTTP_GET,  handleStatus);
  server.on("/type",       HTTP_POST, handleType);
  server.on("/stop",       HTTP_POST, handleStop);
  server.on("/p",          HTTP_POST, handlePush);
  server.on("/latest",     HTTP_GET,  handleLatest);
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
}