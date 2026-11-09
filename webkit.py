"""هسته‌ی مشترک پنل وب: ظاهر (CSS)، قالب صفحه، نشست/دسترسی، ابزارهای فرم و جدول، ثبت مسیرها.
همه‌ی متن‌های قابل‌نمایش با _() ترجمه می‌شوند (زبان هر درخواست از کوکی wl)."""
import csv
import hashlib
import hmac
import io
import json
import time
from datetime import datetime
from urllib.parse import urlencode

from aiohttp import web

from db import TEHRAN
from i18n import N_, SRC_LANG, _, cur_web_lang
from service import PAY_STATUS_FA, STATUS_FA, Shop
from ui import esc as e
from ui import fmt

PER_PAGE = 25
SESSION_TTL = 12 * 3600

CSS = """
:root{--bg:#0a1020;--panel:#111a2e;--panel2:#16213a;--line:#223052;--tx:#e6ecff;--mut:#8a97b8;--acc:#22b8cf;
--ok:#2ea043;--warn:#d29922;--err:#da3633;--pri:#2f81f7;--inp:#0c1426;--hov:rgba(255,255,255,.02);--onacc:#04202a;--sh:rgba(0,0,0,.5);--accbg:rgba(34,184,207,.14)}
html[data-theme=light]{--bg:#f3f5fa;--panel:#fff;--panel2:#eef1f8;--line:#d8deeb;--tx:#1b2438;--mut:#5d6985;--acc:#0e8fa5;--inp:#fff;--hov:rgba(0,0,0,.035);--onacc:#fff;--sh:rgba(20,30,60,.18);--accbg:rgba(14,143,165,.12)}
html[data-theme=light] .badge.ok{color:#1a7f37}html[data-theme=light] .badge.warn{color:#8a5a00}html[data-theme=light] .badge.err{color:#cf222e}html[data-theme=light] .badge.pri{color:#0969da}
*{box-sizing:border-box}html,body{margin:0}
body{background:var(--bg);color:var(--tx);font-family:Vazirmatn,Tahoma,'Segoe UI',system-ui,-apple-system,sans-serif;font-size:14px;line-height:1.7}
a{color:var(--acc);text-decoration:none}a:hover{text-decoration:underline}
.shell{display:flex;min-height:100vh}
.side{width:250px;background:var(--panel);border-inline-end:1px solid var(--line);padding:16px 12px;position:sticky;top:0;height:100vh;overflow:auto;flex-shrink:0}
.brand{display:flex;align-items:center;gap:10px;padding:6px 8px 16px;font-weight:700;font-size:16px}
.brand i{width:36px;height:36px;border-radius:10px;background:linear-gradient(135deg,var(--acc),var(--pri));display:grid;place-items:center;font-style:normal;color:#fff}
.side a.nav{display:flex;gap:10px;align-items:center;padding:9px 12px;margin:2px 0;border-radius:10px;color:var(--tx)}
.side a.nav:hover{background:var(--panel2);text-decoration:none}
.side a.nav.on{background:var(--accbg);color:var(--acc);font-weight:700}
.main{flex:1;min-width:0}
.top{display:flex;align-items:center;gap:12px;padding:12px 22px;border-bottom:1px solid var(--line);background:var(--panel);position:sticky;top:0;z-index:5;flex-wrap:wrap}
.top h1{margin:0;font-size:18px;flex:1}
.langs{display:flex;gap:4px;flex-wrap:wrap}.langs a{padding:2px 9px;border:1px solid var(--line);border-radius:99px;font-size:12px;color:var(--mut)}
.langs a.on{background:var(--acc);color:var(--onacc);border-color:var(--acc);text-decoration:none}
.burger{display:none;background:var(--panel2);border:1px solid var(--line);color:var(--tx);border-radius:8px;padding:6px 12px;cursor:pointer}
.wrap{padding:22px;max-width:1300px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:16px;margin-bottom:18px}
.card h2{margin:0 0 12px;font-size:15px}.card h3{margin:14px 0 8px;font-size:14px;color:var(--acc)}
.split{display:grid;gap:14px;grid-template-columns:minmax(0,1fr) 400px;align-items:start}.split .sticky{position:sticky;top:76px}
.grid{display:grid;gap:14px}.g4{grid-template-columns:repeat(auto-fit,minmax(210px,1fr))}.g2{grid-template-columns:repeat(auto-fit,minmax(340px,1fr))}
.g3{grid-template-columns:repeat(auto-fit,minmax(260px,1fr))}
.stat{background:var(--panel);border:1px solid var(--line);border-top:3px solid var(--acc);border-radius:14px;padding:14px 16px}
.stat small{color:var(--mut);display:block}.stat b{font-size:24px;display:block;margin:2px 0}
.stat.ok{border-top-color:var(--ok)}.stat.warn{border-top-color:var(--warn)}.stat.err{border-top-color:var(--err)}.stat.pri{border-top-color:var(--pri)}
.tw{overflow-x:auto;-webkit-overflow-scrolling:touch;max-width:100%}table{width:100%;border-collapse:collapse;min-width:560px}
th,td{padding:9px 10px;border-bottom:1px solid var(--line);text-align:start;vertical-align:middle;white-space:nowrap}
th{color:var(--mut);font-weight:500;font-size:12px}tr:hover td{background:var(--hov)}
td.wrap2{white-space:normal;min-width:180px}
.badge{display:inline-block;padding:2px 10px;border-radius:99px;font-size:12px;background:var(--panel2);color:var(--mut)}
.badge.ok{background:rgba(46,160,67,.16);color:#56d364}.badge.warn{background:rgba(210,153,34,.16);color:#e3b341}
.badge.err{background:rgba(218,54,51,.16);color:#ff7b72}.badge.pri{background:rgba(47,129,247,.16);color:#79b8ff}
input,select,textarea{background:var(--inp);border:1px solid var(--line);color:var(--tx);border-radius:9px;padding:8px 10px;font:inherit;max-width:100%}
input:focus,select:focus,textarea:focus{outline:2px solid var(--acc)}
input[type=checkbox]{width:18px;height:18px;accent-color:var(--acc);vertical-align:middle}
textarea{width:100%;min-height:96px}input.w{width:100%}input.s{width:96px}input.m{width:160px}input.l{width:260px}
label.f{display:block;margin:10px 0 4px;color:var(--mut);font-size:13px}.hint{color:var(--mut);font-size:12px}
.b{background:var(--panel2);border:1px solid var(--line);color:var(--tx);border-radius:9px;padding:7px 14px;cursor:pointer;font:inherit;display:inline-block}
.b:hover{border-color:var(--acc);text-decoration:none}.b.pri{background:var(--pri);border-color:var(--pri);color:#fff}.b.ok{background:var(--ok);border-color:var(--ok);color:#fff}
.b.err{background:var(--err);border-color:var(--err);color:#fff}.b.sm{padding:3px 10px;font-size:12px}
form.inline{display:inline-block;margin:0 2px}.row{display:flex;gap:10px;flex-wrap:wrap;align-items:center}
.flash{padding:10px 14px;border-radius:10px;margin-bottom:16px;border:1px solid}
.flash.ok{background:rgba(46,160,67,.12);border-color:var(--ok)}.flash.err{background:rgba(218,54,51,.12);border-color:var(--err)}
.flash.warn{background:rgba(210,153,34,.12);border-color:var(--warn)}
.pager{display:flex;gap:6px;flex-wrap:wrap;margin-top:12px}.pager a,.pager span{padding:4px 11px;border:1px solid var(--line);border-radius:8px}
.pager span{background:var(--acc);color:var(--onacc);border-color:var(--acc)}
svg.chart{width:100%;height:auto}.chart .bar{fill:var(--acc)}.chart .lbl{fill:var(--mut);font-size:10px;text-anchor:middle}
.chart .grid{stroke:var(--line)}.chart .ln{fill:none;stroke:var(--acc);stroke-width:2}.chart .pt{fill:var(--acc)}
.login{max-width:400px;margin:10vh auto;padding:0 16px}
.phone{background:#0e1621;border:1px solid var(--line);border-radius:18px;padding:14px;max-width:380px;margin:auto}
.msg{background:#182533;border-radius:12px;padding:10px 12px;margin-bottom:10px;color:#dbe6f3;white-space:pre-wrap;font-size:13px}
.kbrow{display:flex;gap:6px;margin-bottom:6px}
.tgb{flex:1;text-align:center;padding:9px 6px;border-radius:9px;color:#fff;font-size:13px;backdrop-filter:blur(6px);
background:rgba(255,255,255,.14);border:1px solid rgba(255,255,255,.12);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.tgb.primary{background:rgba(47,129,247,.78)}.tgb.success{background:rgba(46,160,67,.80)}.tgb.danger{background:rgba(218,54,51,.80)}
.tgb small{opacity:.7;font-size:10px}
.dot{display:inline-block;width:12px;height:12px;border-radius:50%;vertical-align:middle;margin-inline-end:4px;background:#8b949e}
.dot.primary{background:#2f81f7}.dot.success{background:#2ea043}.dot.danger{background:#da3633}
.perm{display:inline-flex;gap:6px;align-items:center;margin:3px 12px 3px 0;padding:3px 0}
.kv{display:grid;grid-template-columns:max-content 1fr;gap:4px 14px}.kv dt{color:var(--mut)}.kv dd{margin:0}
.tabs{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:14px}.tabs a{padding:5px 14px;border:1px solid var(--line);border-radius:99px;color:var(--tx)}
.tabs a.on{background:var(--acc);color:var(--onacc);border-color:var(--acc);font-weight:700;text-decoration:none}
code,.mono{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;direction:ltr;unicode-bidi:embed}
pre.pv{background:var(--inp);border:1px solid var(--line);border-radius:10px;padding:10px;white-space:pre-wrap;word-break:break-all;direction:ltr;text-align:left;max-height:320px;overflow:auto}
.bar-h{height:8px;border-radius:99px;background:var(--panel2);overflow:hidden}.bar-h i{display:block;height:100%;background:var(--acc)}
.side-close{display:none;position:absolute;top:10px;inset-inline-end:10px;background:var(--panel2);border:1px solid var(--line);color:var(--tx);border-radius:8px;width:34px;height:34px;font-size:18px;cursor:pointer;line-height:1}
.side-bd{display:none;position:fixed;inset:0;background:rgba(0,0,0,.55);z-index:19}
.thm{display:flex;gap:6px;align-items:center;margin-top:10px}.thm button{flex:1;background:var(--panel2);border:1px solid var(--line);color:var(--tx);border-radius:10px;padding:7px 0;font-size:17px;cursor:pointer;line-height:1}
.thm button.on{border-color:var(--acc);background:var(--accbg)}
.pick{margin-top:8px;padding:10px;border:1px solid var(--line);border-radius:12px;background:var(--panel2)}.pick[hidden]{display:none}.pick h4{margin:0 0 8px;font-size:12px;color:var(--mut);font-weight:500}
.sw{display:flex;gap:7px;flex-wrap:wrap}.sw button{width:26px;height:26px;border-radius:50%;border:2px solid transparent;cursor:pointer;padding:0}.sw button.on{border-color:var(--tx)}
.pick .rst{margin-top:8px;width:100%;font-size:12px;padding:4px 0}.pick input[type=color]{width:34px;height:26px;padding:0;border:0;background:none;vertical-align:middle}
.thm-fix{position:fixed;top:10px;inset-inline-end:10px;z-index:30;width:200px}
.mini{display:inline-flex;flex-direction:column;gap:2px;font-size:11px;color:var(--mut);margin:0 0 4px}.mini input{width:100%}
@media(max-width:1150px){.split{grid-template-columns:1fr}.split .sticky{position:static}}
@media(max-width:900px){
.side{position:fixed;inset-inline-start:-290px;top:0;z-index:20;width:270px;max-width:86vw;transition:inset-inline-start .2s;box-shadow:0 0 30px var(--sh)}
.side.open{inset-inline-start:0}.side.open~.side-bd,body.nav-open .side-bd{display:block}
.side-close{display:block}.burger{display:block}.wrap{padding:14px}.top{padding:10px 14px}
table.rt{min-width:0;display:block}.rt thead{display:none}.rt tbody{display:block}
.rt tr{display:block;border:1px solid var(--line);border-radius:12px;margin-bottom:10px;padding:6px 10px;background:var(--panel2)}
.rt td{display:flex;justify-content:space-between;align-items:center;gap:10px;border:0;border-bottom:1px dashed var(--line);white-space:normal;padding:7px 2px;flex-wrap:wrap}
.rt td:last-child{border-bottom:0}.rt td::before{content:attr(data-label);color:var(--mut);font-size:12px;flex-shrink:0}
.rt td[data-label='']::before,.rt td:not([data-label])::before{display:none}
.rt td form{display:flex;flex-wrap:wrap;gap:8px;width:100%;align-items:flex-end}.rt td form>input,.rt td form>select{flex:1 1 130px;min-width:0;width:auto}.rt td .mini{flex:1 1 130px;min-width:0}.rt td .mini input{width:100%}
.rt td[data-label='ویرایش']::before{display:none}
}
/* ======== پوسته‌ی «شیشه‌ی شفق» (مشترک با مینی‌اپ) ======== */
:root{--bg:#080c1a;--panel:rgba(255,255,255,.065);--panel2:rgba(255,255,255,.09);--line:rgba(255,255,255,.12);--edge:rgba(255,255,255,.2);--inp:rgba(8,12,28,.55);
--g1:#8b5cf6;--g2:#22d3ee;--g3:#f472b6;--gsh:0 14px 38px rgba(2,4,20,.5)}
html[data-theme=light]{--bg:#e9edfb;--panel:rgba(255,255,255,.62);--panel2:rgba(255,255,255,.8);--line:rgba(60,70,140,.15);--edge:rgba(255,255,255,.95);--inp:rgba(255,255,255,.85);--gsh:0 12px 30px rgba(70,80,160,.16)}
html{background:var(--bg)}
body{position:relative;overflow-x:hidden;background:transparent}
body::before,body::after{content:"";position:fixed;z-index:-1;pointer-events:none;width:80vmax;height:80vmax;border-radius:50%;filter:blur(80px);opacity:.5;animation:aur 30s ease-in-out infinite alternate}
body::before{top:-30vmax;inset-inline-start:-25vmax;background:radial-gradient(circle,var(--g1) 0,transparent 60%)}
body::after{bottom:-35vmax;inset-inline-end:-25vmax;background:radial-gradient(circle,var(--acc) 0,transparent 60%);animation-delay:-11s}
html[data-theme=light] body::before,html[data-theme=light] body::after{opacity:.32}
@keyframes aur{to{transform:translate(6vmax,5vmax) rotate(20deg)}}
@media (prefers-reduced-motion:reduce){body::before,body::after{animation:none}}
.side,.top,.card,.stat,.pick,.phone{-webkit-backdrop-filter:blur(22px) saturate(1.5);backdrop-filter:blur(22px) saturate(1.5)}
.side{background:linear-gradient(180deg,rgba(255,255,255,.09),rgba(255,255,255,.04));border-inline-end:1px solid var(--edge)}
.top{background:var(--panel);border-bottom:1px solid var(--edge)}
.card{border:1px solid var(--edge);border-radius:20px;box-shadow:var(--gsh),inset 0 1px 0 var(--edge);background:linear-gradient(160deg,var(--panel2),var(--panel))}
.stat{position:relative;overflow:hidden;border:1px solid var(--edge);border-top:1px solid var(--edge);border-radius:20px;box-shadow:var(--gsh),inset 0 1px 0 var(--edge);background:linear-gradient(160deg,var(--panel2),var(--panel))}
.stat::before{content:"";position:absolute;inset-inline:0;bottom:-55%;height:90%;background:radial-gradient(ellipse at 50% 100%,var(--sc,var(--acc)),transparent 70%);opacity:.28;pointer-events:none}
.stat.ok,.stat.warn,.stat.err,.stat.pri{border-top-color:var(--edge)}
.stat.ok{--sc:var(--ok)}.stat.warn{--sc:var(--warn)}.stat.err{--sc:var(--err)}.stat.pri{--sc:var(--pri)}
.stat b{font-size:26px;background:linear-gradient(90deg,var(--tx),color-mix(in srgb,var(--tx) 60%,var(--sc,var(--acc))));-webkit-background-clip:text;background-clip:text;color:transparent}
.brand i{box-shadow:0 8px 22px color-mix(in srgb,var(--acc) 55%,transparent);background:linear-gradient(135deg,var(--acc),var(--g1))}
.side a.nav{border:1px solid transparent;transition:background .15s,border-color .15s}
.side a.nav:hover{background:var(--panel2);border-color:var(--line)}
.side a.nav.on{background:linear-gradient(135deg,color-mix(in srgb,var(--acc) 28%,transparent),color-mix(in srgb,var(--g1) 22%,transparent));border-color:var(--edge);color:var(--tx);box-shadow:0 6px 16px color-mix(in srgb,var(--acc) 22%,transparent)}
.b{background:var(--panel2);border:1px solid var(--edge);border-radius:12px;transition:transform .12s,box-shadow .15s,border-color .15s}
.b:hover{box-shadow:0 6px 18px color-mix(in srgb,var(--acc) 30%,transparent)}.b:active{transform:scale(.97)}
.b.pri{background:linear-gradient(135deg,var(--pri),var(--g1));border-color:transparent;box-shadow:0 8px 20px color-mix(in srgb,var(--pri) 38%,transparent),inset 0 1px 0 rgba(255,255,255,.3)}
.b.ok{background:linear-gradient(135deg,#10b981,#059669);border-color:transparent}.b.err{background:linear-gradient(135deg,#f43f5e,#be123c);border-color:transparent}
input,select,textarea{background:var(--inp);border:1px solid var(--line);border-radius:12px;-webkit-backdrop-filter:blur(10px);backdrop-filter:blur(10px)}
input:focus,select:focus,textarea:focus{outline:none;border-color:var(--acc);box-shadow:0 0 0 3px color-mix(in srgb,var(--acc) 28%,transparent)}
th{border-bottom-color:var(--edge)}td{border-bottom-color:var(--line)}
.badge{border:1px solid var(--line)}
.tabs a,.langs a,.pager a{background:var(--panel);-webkit-backdrop-filter:blur(10px);backdrop-filter:blur(10px)}
.tabs a.on,.langs a.on,.pager span{background:linear-gradient(135deg,var(--acc),var(--g1));border-color:transparent;color:#fff;box-shadow:0 6px 16px color-mix(in srgb,var(--acc) 35%,transparent)}
.flash{border-radius:14px;-webkit-backdrop-filter:blur(12px);backdrop-filter:blur(12px)}
.login .card{box-shadow:0 24px 60px rgba(2,4,20,.5),inset 0 1px 0 var(--edge)}
.top h1{font-size:19px;letter-spacing:-.2px}
.side::-webkit-scrollbar,.tw::-webkit-scrollbar{height:8px;width:8px}.side::-webkit-scrollbar-thumb,.tw::-webkit-scrollbar-thumb{background:var(--line);border-radius:9px}
@media(max-width:900px){.side{background:var(--bg);backdrop-filter:none;-webkit-backdrop-filter:none}.rt tr{background:var(--panel2)}}
/* ---- منوی گروه‌بندی‌شده ---- */
.side{display:flex;flex-direction:column;padding:14px 10px 10px}
.nav-wrap{flex:1;overflow:auto;margin:0 -4px;padding:2px 4px}
.nsearch{padding:0 4px 10px}.nsearch input{width:100%;padding:8px 12px;font-size:13px;border-radius:10px}
.ng{margin:0 0 4px}.ng.hid,.side a.nav.hid{display:none}
.nh{width:100%;display:flex;align-items:center;justify-content:space-between;background:none;border:0;color:var(--mut);font:inherit;font-size:11.5px;font-weight:600;letter-spacing:.3px;padding:10px 10px 5px;cursor:pointer}
.nh:hover{color:var(--tx)}.chev{width:7px;height:7px;border-right:1.5px solid currentColor;border-bottom:1.5px solid currentColor;transform:rotate(45deg);transition:transform .18s;margin-top:-3px}
.ng.shut .chev{transform:rotate(-45deg)}.ng.shut .nl{display:none}
.nl{display:flex;flex-direction:column}
.side a.nav{padding:8px 11px;margin:1px 0;font-size:13.5px;gap:11px;color:var(--tx)}
.side a.nav .ni{display:grid;place-items:center;color:var(--mut);flex-shrink:0}.side a.nav:hover .ni,.side a.nav.on .ni{color:var(--acc)}
.side a.nav.on{font-weight:700}
.nfoot{margin-top:8px;border-top:1px solid var(--line);padding-top:8px}
.crumb{display:inline-block;font-size:11.5px;font-weight:500;color:var(--mut);background:var(--panel2);border:1px solid var(--line);border-radius:99px;padding:1px 10px;margin-inline-end:10px;vertical-align:middle}
/* ---- تب‌های هر بخش ---- */
.stabs{display:flex;gap:6px;flex-wrap:wrap;align-items:center;margin-bottom:16px;padding:6px;border:1px solid var(--line);border-radius:14px;background:var(--panel)}
.stabs .sg{font-size:11px;color:var(--mut);padding:0 8px 0 4px}.stabs .sep{width:1px;align-self:stretch;background:var(--line);margin:3px 4px}
.stabs a{padding:6px 14px;border-radius:10px;font-size:13px;color:var(--tx);border:1px solid transparent}
.stabs a:hover{background:var(--panel2);text-decoration:none}
.stabs a.on{background:linear-gradient(135deg,var(--acc),var(--g1,var(--pri)));color:#fff;box-shadow:0 6px 16px color-mix(in srgb,var(--acc) 30%,transparent)}
/* ---- کارت‌ها: عنوان با نوار رنگی و فاصله‌ی بهتر ---- */
.card h2{display:flex;align-items:center;gap:8px;padding-bottom:10px;margin-bottom:14px;border-bottom:1px solid var(--line);font-size:15px}
.card h2::before{content:"";width:4px;height:16px;border-radius:4px;background:linear-gradient(var(--acc),var(--g1,var(--pri)));flex-shrink:0}
.wrap{padding:22px 26px}
"""

THEME_JS = """
(function(){var D=document.documentElement,AC=['#22b8cf','#2f81f7','#2ea043','#a371f7','#f0883e','#ec6cb9','#f85149'];
function lum(h){var r=parseInt(h.substr(1,2),16),g=parseInt(h.substr(3,2),16),b=parseInt(h.substr(5,2),16);return(0.299*r+0.587*g+0.114*b)/255}
function get(k){try{return localStorage.getItem(k)}catch(e){return null}}function put(k,v){try{if(v===null)localStorage.removeItem(k);else localStorage.setItem(k,v)}catch(e){}}
function mark(){var t=D.getAttribute('data-theme')||'dark',a=get('ss_accent')||'';
document.querySelectorAll('[data-th]').forEach(function(b){b.classList.toggle('on',b.getAttribute('data-th')===t)});
document.querySelectorAll('.sw button').forEach(function(b){b.classList.toggle('on',b.getAttribute('data-c')===a)})}
window.ssTheme=function(t){if(t!=='light'&&t!=='dark')t='dark';D.setAttribute('data-theme',t);put('ss_theme',t);mark()};
window.ssAccent=function(h){if(!h||!/^#[0-9a-f]{6}$/i.test(h)){D.style.removeProperty('--acc');D.style.removeProperty('--pri');D.style.removeProperty('--onacc');D.style.removeProperty('--accbg');put('ss_accent',null);mark();return}
h=h.toLowerCase();D.style.setProperty('--acc',h);D.style.setProperty('--pri',h);D.style.setProperty('--onacc',lum(h)>0.62?'#04202a':'#fff');
D.style.setProperty('--accbg',h+'24');put('ss_accent',h);mark()};
window.ssPick=function(){var p=document.getElementById('ssp');if(p)p.hidden=!p.hidden};
window.ssSwatches=function(){var el=document.querySelector('.sw');if(!el||el.children.length)return;AC.forEach(function(c){var b=document.createElement('button');b.type='button';b.style.background=c;b.setAttribute('data-c',c);b.onclick=function(){ssAccent(c)};el.appendChild(b)});mark()};
var t=get('ss_theme');D.setAttribute('data-theme',t==='light'?'light':'dark');var a=get('ss_accent');if(a)ssAccent(a);
document.addEventListener('DOMContentLoaded',function(){ssSwatches();mark()});})();
"""

BASE_JS = """
function ngTog(b){var g=b.parentNode;g.classList.toggle('shut');try{var m=JSON.parse(localStorage.getItem('ss_ng')||'{}');m[g.getAttribute('data-g')]=g.classList.contains('shut')?1:0;localStorage.setItem('ss_ng',JSON.stringify(m))}catch(e){}}
function ngFilter(q){q=(q||'').trim().toLowerCase();document.querySelectorAll('#navw .ng').forEach(function(g){var any=false;g.querySelectorAll('a.nav').forEach(function(a){var ok=!q||(a.getAttribute('data-q')||a.textContent).toLowerCase().indexOf(q)>-1;a.classList.toggle('hid',!ok);if(ok)any=true});g.classList.toggle('hid',!any);if(q&&any)g.classList.remove('shut')})}
(function(){try{var m=JSON.parse(localStorage.getItem('ss_ng')||'{}');document.querySelectorAll('#navw .ng[data-g]').forEach(function(g){if(m[g.getAttribute('data-g')]&&!g.classList.contains('hasact'))g.classList.add('shut')})}catch(e){}})();
function navToggle(f){var sd=document.getElementById('side');if(!sd)return;var o=(f===undefined)?!sd.classList.contains('open'):f;sd.classList.toggle('open',o);document.body.classList.toggle('nav-open',o)}
document.addEventListener('keydown',function(e){if(e.key==='Escape')navToggle(false)});
document.addEventListener('click',function(e){var a=e.target.closest('#side a.nav');if(a)navToggle(false)});
window.addEventListener('resize',function(){if(window.innerWidth>900)navToggle(false)});
document.addEventListener('submit',function(ev){var c=ev.target.getAttribute('data-confirm');if(c&&!confirm(c))ev.preventDefault()});
document.addEventListener('click',function(ev){var a=ev.target.closest('[data-confirm-link]');if(a&&!confirm(a.getAttribute('data-confirm-link')))ev.preventDefault()});
"""


# ---------------------------------------------------------------- ابزارهای عمومی
def g2j(gy, gm, gd):
    g_d_m = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]
    if gy > 1600:
        jy, gy = 979, gy - 1600
    else:
        jy, gy = 0, gy - 621
    gy2 = gy + 1 if gm > 2 else gy
    days = 365 * gy + (gy2 + 3) // 4 - (gy2 + 99) // 100 + (gy2 + 399) // 400 - 80 + gd + g_d_m[gm - 1]
    jy += 33 * (days // 12053)
    days %= 12053
    jy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        jy += (days - 1) // 365
        days = (days - 1) % 365
    if days < 186:
        jm, jd = 1 + days // 31, 1 + days % 31
    else:
        jm, jd = 7 + (days - 186) // 30, 1 + (days - 186) % 30
    return jy, jm, jd


def fdate(ts):
    """تاریخ: برای فارسی شمسی، برای سایر زبان‌ها میلادی (به وقت تهران)."""
    if not ts:
        return "—"
    d = datetime.fromtimestamp(ts, TEHRAN)
    if cur_web_lang() == SRC_LANG:
        jy, jm, jd = g2j(d.year, d.month, d.day)
        return f"{jy}/{jm:02d}/{jd:02d} {d.hour:02d}:{d.minute:02d}"
    return d.strftime("%Y-%m-%d %H:%M")


def to_int(v, default=None):
    try:
        return int(str(v).translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")).replace(",", "").replace("٬", "").strip())
    except (TypeError, ValueError):
        return default


def to_float(v, default=None):
    try:
        return float(str(v).translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩٫", "01234567890123456789.")).replace(",", "").replace("٬", "").strip())
    except (TypeError, ValueError):
        return default


def badge(text, kind=""):
    return f"<span class='badge {kind}'>{e(text)}</span>"


STATUS_KIND = {"done": "ok", "confirmed": "ok", "paid": "warn", "pending_payment": "warn", "pending": "warn",
               "waiting_admin": "warn", "manual_pending": "warn", "delivering": "warn", "failed": "err",
               "rejected": "err", "cancelled": "", "expired": "", "refunded": "err"}


def status_badge(s, pay=False):
    txt = _((PAY_STATUS_FA if pay else STATUS_FA).get(s, s))
    return badge(txt, STATUS_KIND.get(s, ""))


def safe_next(v, default):
    return v if isinstance(v, str) and v.startswith("/") and not v.startswith("//") and "\\" not in v and not any(ord(ch) < 32 for ch in v) else default


def redirect(path, ok=None, err=None):
    q = {}
    if ok:
        q["ok"] = ok
    if err:
        q["err"] = err
    if q:
        path += ("&" if "?" in path else "?") + urlencode(q)
    return web.HTTPSeeOther(path)


def tbl(headers, rows, empty=None):
    if not rows:
        return f"<p class='hint'>{e(empty or _('موردی وجود ندارد.'))}</p>"
    th = "".join(f"<th>{e(h)}</th>" for h in headers)
    tr = "".join("<tr>" + "".join(f"<td data-label=\"{e(headers[i]) if i < len(headers) else ''}\">{c}</td>" for i, c in enumerate(r)) + "</tr>" for r in rows)
    return f"<div class='tw'><table class='rt'><thead><tr>{th}</tr></thead><tbody>{tr}</tbody></table></div>"


def pager(req, total, page, per=PER_PAGE, key="p"):
    pages = max(1, (total + per - 1) // per)
    if pages <= 1:
        return ""
    base = {k: v for k, v in req.query.items() if k not in (key, "ok", "err")}
    out = []
    for i in range(1, pages + 1):
        if pages > 12 and abs(i - page) > 3 and i not in (1, pages):
            continue
        q = urlencode({**base, key: i})
        out.append(f"<span>{i}</span>" if i == page else f"<a href='{e(req.path)}?{e(q)}'>{i}</a>")
    return f"<div class='pager'>{''.join(out)}</div>"


def page_no(req):
    return max(1, to_int(req.query.get("p"), 1) or 1)


def csrf(req):
    return f"<input type='hidden' name='_csrf' value='{req['csrf']}'>"


def pform(req, action, inner, cls="inline", confirm=None, multipart=False):
    oc = f" data-confirm=\"{e(confirm)}\"" if confirm else ""
    enc = " enctype='multipart/form-data'" if multipart else ""
    return f"<form method='post' action='{action}' class='{cls}'{oc}{enc}>{csrf(req)}{inner}</form>"


def hidden(name, value):
    return f"<input type='hidden' name='{e(name)}' value='{e(value)}'>"


def inp(name, value="", ph="", cls="m", typ="text", required=False, extra=""):
    return (f"<input class='{cls}' type='{typ}' name='{e(name)}' value='{e(value)}'"
            + (f" placeholder='{e(ph)}'" if ph else "") + (" required" if required else "") + f" {extra}>")


def sel(name, options, cur="", cls=""):
    """options: لیست (value, label)"""
    o = "".join(f"<option value='{e(v)}' {'selected' if str(v) == str(cur) else ''}>{e(lab)}</option>" for v, lab in options)
    return f"<select name='{e(name)}' class='{cls}'>{o}</select>"


def user_link(uid, username=None):
    label = f"@{e(username)}" if username else str(uid)
    return f"<a href='/users/{uid}'>{label}</a> <span class='hint'>{uid if username else ''}</span>"


def csv_response(filename, header, rows):
    """خروجی CSV (UTF-8 با BOM برای اکسل). سلول‌هایی که با = + - @ شروع شوند برای جلوگیری از تزریق فرمول پیشوند می‌گیرند."""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    for r in rows:
        w.writerow([("'" + str(c)) if isinstance(c, str) and c[:1] in ("=", "+", "-", "@") else c for c in r])
    return web.Response(body=("﻿" + buf.getvalue()).encode("utf-8"), content_type="text/csv",
                        headers={"Content-Disposition": f"attachment; filename={filename}"})


def bar_chart(series, unit=None):
    w, h, pad = 720, 210, 26
    unit = unit or _("تومان")
    mx = max([s for _d, s, _c in series] + [1])
    bw = (w - 2 * pad) / len(series)
    parts = [f"<line class='grid' x1='{pad}' x2='{w - pad}' y1='{h - pad}' y2='{h - pad}'/>"]
    for i, (d, s, c) in enumerate(series):
        bh = (h - 2 * pad - 10) * s / mx
        x = pad + i * bw + 4
        parts.append(f"<rect class='bar' x='{x:.1f}' y='{h - pad - bh:.1f}' width='{bw - 8:.1f}' height='{max(bh, 1):.1f}' rx='4'>"
                     f"<title>{e(d)}: {fmt(s)} {e(unit)} ({c})</title></rect>")
        parts.append(f"<text class='lbl' x='{x + (bw - 8) / 2:.1f}' y='{h - 9}'>{e(d)}</text>")
    return f"<svg class='chart' viewBox='0 0 {w} {h}'>{''.join(parts)}</svg>"


def line_chart(points, label=""):
    """points: لیست (ts, value)"""
    if len(points) < 2:
        return f"<p class='hint'>{e(_('داده‌ی کافی برای نمودار وجود ندارد.'))}</p>"
    w, h, pad = 720, 190, 28
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    x0, x1 = min(xs), max(xs)
    y0, y1 = min(ys), max(ys)
    if y1 == y0:
        y1 = y0 + 1
    px = lambda x: pad + (x - x0) / max(x1 - x0, 1) * (w - 2 * pad)  # noqa: E731
    py = lambda y: h - pad - (y - y0) / (y1 - y0) * (h - 2 * pad)  # noqa: E731
    d = " ".join(f"{'M' if i == 0 else 'L'}{px(x):.1f},{py(y):.1f}" for i, (x, y) in enumerate(points))
    last = points[-1]
    return (f"<svg class='chart' viewBox='0 0 {w} {h}'><line class='grid' x1='{pad}' x2='{w - pad}' y1='{h - pad}' y2='{h - pad}'/>"
            f"<path class='ln' d='{d}'/><circle class='pt' cx='{px(last[0]):.1f}' cy='{py(last[1]):.1f}' r='4'/>"
            f"<text class='lbl' x='{pad}' y='{h - 9}'>{fmt(y0)}</text><text class='lbl' x='{w - pad}' y='{h - 9}'>{fmt(y1)}</text>"
            f"<text class='lbl' x='{w / 2}' y='14'>{e(label)}</text></svg>")


# ---------------------------------------------------------------- مسیرها و دسترسی
ROUTES: list[tuple] = []


def route(method, path, perm=None):
    def deco(fn):
        ROUTES.append((method, path, fn, perm))
        return fn
    return deco


def get(path, perm=None):
    return route("GET", path, perm)


def post(path, perm=None):
    return route("POST", path, perm)


def can(req, perm) -> bool:
    p = req.get("perms")
    if p is None:
        return False
    return perm is None or p == "*" or perm in p


def need(req, perm):
    if not can(req, perm):
        raise redirect("/", err=_("شما به این بخش دسترسی ندارید."))


def guarded(handler, perm):
    async def wrapper(request):
        if perm and not can(request, perm):
            raise redirect("/", err=_("شما به این بخش دسترسی ندارید."))
        return await handler(request)
    wrapper.__name__ = handler.__name__
    return wrapper


# (آدرس, عنوان, آیکن, مجوز)
NAV = [
    ("/", N_("داشبورد"), "📊", "stats"),
    ("/users", N_("کاربران"), "👥", "users_view"),
    ("/orders", N_("سفارش‌ها"), "🧾", "orders"),
    ("/reports", N_("گزارش‌ها و آمار"), "📈", "stats"),
    ("/payments", N_("پرداخت‌ها و رسیدها"), "💳", "receipts"),
    ("/wallet", N_("تراکنش‌های کیف پول"), "👛", "wallet"),
    ("/referrals", N_("زیرمجموعه‌ها و جایزه‌ها"), "🤝", "users_view"),
    ("/products", N_("محصولات و قیمت"), "📦", "products"),
    ("/rates", N_("نرخ‌ها و سود"), "💹", "pricing"),
    ("/stars", N_("ستاره و تحویل خودکار"), "⭐", "stars"),
    ("/marketapp", N_("Marketapp (Fragment)"), "🛰", "pricing"),
    ("/vpn", N_("فروش VPN"), "🛡", "vpn"),
    ("/discounts", N_("کدهای تخفیف"), "🏷", "discounts"),
    ("/broadcast", N_("پیام همگانی"), "📢", "broadcast"),
    ("/keyboard", N_("دکمه‌ها، رنگ و ایموجی"), "🎨", "buttons"),
    ("/languages", N_("زبان‌ها و ترجمه‌ها"), "🌐", "languages"),
    ("/settings", N_("تنظیمات"), "⚙️", "*any_settings"),
    ("/admins", N_("ادمین‌ها و دسترسی‌ها"), "👮", "admins"),
    ("/logs", N_("لاگ فعالیت"), "📜", "logs"),
    ("/backup", N_("بکاپ و وارد کردن بکاپ"), "💾", "backup"),
]


# گروه‌بندی منو: (عنوان گروه, [آدرس‌ها]) — هیچ صفحه‌ای حذف نشده؛ فقط مرتب و جدا شده است
NAV_GROUPS = [
    (N_("عمومی"), ["/", "/users", "/orders", "/reports"]),
    (N_("مالی"), ["/payments", "/wallet", "/referrals"]),
    (N_("فروشگاه"), ["/products", "/rates", "/stars", "/marketapp", "/discounts"]),
    (N_("VPN و نمایندگی"), ["/vpn"]),
    (N_("ظاهر و پیام"), ["/keyboard", "/languages", "/broadcast"]),
    (N_("سیستم"), ["/settings", "/admins", "/logs", "/backup"]),
]

_I = lambda d: f"<svg viewBox='0 0 24 24' width='18' height='18' fill='none' stroke='currentColor' stroke-width='1.8' stroke-linecap='round' stroke-linejoin='round' aria-hidden='true'>{d}</svg>"
NAV_ICONS = {
    "/": _I("<rect x='3' y='3' width='7' height='9' rx='1.5'/><rect x='14' y='3' width='7' height='5' rx='1.5'/><rect x='14' y='12' width='7' height='9' rx='1.5'/><rect x='3' y='16' width='7' height='5' rx='1.5'/>"),
    "/users": _I("<circle cx='9' cy='8' r='3.5'/><path d='M2.5 20c.6-3.6 3.2-5.5 6.5-5.5s5.9 1.9 6.5 5.5'/><path d='M16 4.6a3.5 3.5 0 0 1 0 6.8M18 14.7c2 .6 3.2 2.3 3.5 5.3'/>"),
    "/orders": _I("<path d='M6 3h12v18l-3-2-3 2-3-2-3 2z'/><path d='M9 8h6M9 12h6'/>"),
    "/reports": _I("<path d='M3 20h18'/><path d='M6 16V9M11 16V5M16 16v-6M21 16V8'/>"),
    "/payments": _I("<rect x='2.5' y='5' width='19' height='14' rx='2.5'/><path d='M2.5 10h19M6 15h4'/>"),
    "/wallet": _I("<path d='M3 7.5A2.5 2.5 0 0 1 5.5 5H19v4'/><path d='M3 7.5V18a2 2 0 0 0 2 2h15a1 1 0 0 0 1-1v-9a1 1 0 0 0-1-1H5.5A2.5 2.5 0 0 1 3 7.5z'/><circle cx='16.5' cy='14.5' r='1.2'/>"),
    "/referrals": _I("<circle cx='6' cy='12' r='2.5'/><circle cx='18' cy='6' r='2.5'/><circle cx='18' cy='18' r='2.5'/><path d='M8.2 10.8l7.6-3.6M8.2 13.2l7.6 3.6'/>"),
    "/products": _I("<path d='M21 8l-9-5-9 5 9 5z'/><path d='M3 8v8l9 5 9-5V8M12 13v8'/>"),
    "/rates": _I("<path d='M3 17l6-6 4 4 8-8'/><path d='M15 7h6v6'/>"),
    "/stars": _I("<path d='M12 3l2.7 5.6 6.1.9-4.4 4.3 1 6.1L12 17l-5.4 2.9 1-6.1L3.2 9.5l6.1-.9z'/>"),
    "/marketapp": _I("<circle cx='12' cy='12' r='9'/><path d='M3 12h18M12 3c2.5 2.7 3.8 5.7 3.8 9S14.500 18.300 12 21c-2.500-2.700-3.800-5.700-3.800-9S9.500 5.700 12 3z'/>"),
    "/discounts": _I("<path d='M20.5 13.5l-7 7a2 2 0 0 1-2.8 0L3 12.8V4h8.8l8.700 8.700a2 2 0 0 1 0 .8z'/><circle cx='7.500' cy='8.500' r='1.300'/>"),
    "/vpn": _I("<path d='M12 3l8 3v6c0 4.500-3.300 8-8 9-4.700-1-8-4.500-8-9V6z'/><path d='M9 12l2 2 4-4'/>"),
    "/keyboard": _I("<rect x='2.500' y='6' width='19' height='12' rx='2.500'/><path d='M6 10h.01M10 10h.01M14 10h.01M18 10h.01M7 14h10'/>"),
    "/languages": _I("<path d='M4 5h9M8.500 3v2M5.500 5c.5 3.500 3 6.500 6.500 8M11.500 5c-.5 3.500-3 6.500-6.500 8'/><path d='M13 20l4-9 4 9M14.500 17h5'/>"),
    "/broadcast": _I("<path d='M3 11v2a1 1 0 0 0 1 1h3l8 4V6L7 10H4a1 1 0 0 0-1 1z'/><path d='M18.500 9a4 4 0 0 1 0 6'/>"),
    "/settings": _I("<circle cx='12' cy='12' r='3'/><path d='M19.400 15a1.700 1.700 0 0 0 .3 1.800l.1.1a2 2 0 1 1-2.800 2.800l-.1-.1a1.700 1.700 0 0 0-1.800-.3 1.700 1.700 0 0 0-1 1.500V21a2 2 0 1 1-4 0v-.1a1.700 1.700 0 0 0-1.100-1.500 1.700 1.700 0 0 0-1.800.3l-.1.1a2 2 0 1 1-2.800-2.800l.1-.1a1.700 1.700 0 0 0 .3-1.800 1.700 1.700 0 0 0-1.500-1H3a2 2 0 1 1 0-4h.1a1.700 1.700 0 0 0 1.500-1.100 1.700 1.700 0 0 0-.3-1.800l-.1-.1a2 2 0 1 1 2.800-2.800l.1.1a1.700 1.700 0 0 0 1.800.3h0a1.700 1.700 0 0 0 1-1.500V3a2 2 0 1 1 4 0v.1a1.700 1.700 0 0 0 1 1.500h0a1.700 1.700 0 0 0 1.800-.3l.1-.1a2 2 0 1 1 2.800 2.800l-.1.1a1.700 1.700 0 0 0-.3 1.800v0a1.700 1.700 0 0 0 1.500 1H21a2 2 0 1 1 0 4h-.1a1.700 1.700 0 0 0-1.500 1z'/>"),
    "/admins": _I("<path d='M12 3l8 3v6c0 4.500-3.300 8-8 9-4.700-1-8-4.500-8-9V6z'/><circle cx='12' cy='10' r='2.200'/><path d='M8 16c.700-1.800 2.200-2.600 4-2.600s3.300.8 4 2.600'/>"),
    "/logs": _I("<path d='M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z'/><path d='M14 3v6h6M8 13h8M8 17h6'/>"),
    "/backup": _I("<ellipse cx='12' cy='5.500' rx='8' ry='3'/><path d='M4 5.500v13c0 1.700 3.600 3 8 3s8-1.300 8-3v-13M4 12c0 1.700 3.600 3 8 3s8-1.300 8-3'/>"),
    "/app": _I("<path d='M4 7h16l-1.500 12a2 2 0 0 1-2 1.700H7.500a2 2 0 0 1-2-1.700z'/><path d='M8.500 10V6.500a3.500 3.500 0 0 1 7 0V10'/>"),
}
_I_LOGOUT = _I("<path d='M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4'/><path d='M16 17l5-5-5-5M21 12H9'/>")


def render_nav(req, active: str, shop) -> str:
    """منوی گروه‌بندی‌شده: هر گروه هدر جدا و قابل بستن دارد؛ هر آیتمی که در هیچ گروهی نباشد در «سایر» می‌آید."""
    allowed = {href: (label, perm) for href, label, _ic, perm in NAV if nav_allowed(req, perm)}
    done, out = set(), ""
    groups = list(NAV_GROUPS)
    rest = [h for h in allowed if not any(h in g[1] for g in groups)]
    if rest:
        groups.append((N_("سایر"), rest))
    for title, hrefs in groups:
        items = [h for h in hrefs if h in allowed and h not in done]
        if not items:
            continue
        done.update(items)
        links = "".join(f"<a class='nav {'on' if h == active else ''}' href='{h}' data-q='{e(_(allowed[h][0]))}'>"
                        f"<span class='ni'>{NAV_ICONS.get(h, '')}</span><span class='nt'>{e(_(allowed[h][0]))}</span></a>" for h in items)
        out += (f"<div class='ng{' hasact' if active in items else ''}' data-g='{e(title)}'><button type='button' class='nh' onclick='ngTog(this)'>"
                f"<span>{e(_(title))}</span><i class='chev'></i></button><div class='nl'>{links}</div></div>")
    if shop.miniapp_url():     # بازگشت به فروشگاه (مینی‌اپ) برای ادمینی که از داخل تلگرام آمده
        out += (f"<div class='ng'><a class='nav' href='/app'><span class='ni'>{NAV_ICONS['/app']}</span><span class='nt'>{e(_('فروشگاه (مینی‌اپ)'))}</span></a></div>")
    return out


def nav_crumb(active: str) -> str:
    for title, hrefs in NAV_GROUPS:
        if active in hrefs:
            return f"<span class='crumb'>{e(_(title))}</span>"
    return ""


def nav_allowed(req, perm) -> bool:
    if perm == "*any_settings":
        return can(req, "settings") or can(req, "pricing")
    return can(req, perm)


def first_allowed(req):
    for href, _l, _i, perm in NAV:
        if href != "/" and nav_allowed(req, perm):
            return href
    return None


def theme_ctrl(extra_cls=""):
    """دکمه‌های خورشید/ماه + انتخاب رنگ تم (در نوار کناری و صفحه‌ی ورود)."""
    return (f"<div class='thm {extra_cls}'><button type='button' data-th='light' onclick=\"ssTheme('light')\" title='{e(_('تم روشن'))}'>☀️</button>"
            f"<button type='button' data-th='dark' onclick=\"ssTheme('dark')\" title='{e(_('تم تیره'))}'>🌙</button>"
            f"<button type='button' onclick='ssPick()' title='{e(_('رنگ تم'))}'>🎨</button></div>"
            f"<div class='pick' id='ssp' hidden><h4>{e(_('رنگ اصلی پنل'))}</h4><div class='sw'></div>"
            f"<p style='margin:8px 0 0'><input type='color' id='ssc' value='#22b8cf' oninput=\"ssAccent(this.value)\"> <span class='hint'>{e(_('رنگ دلخواه'))}</span></p>"
            f"<button type='button' class='b sm rst' onclick=\"ssAccent('')\">{e(_('بازنشانی رنگ'))}</button></div>")


async def layout(req, title, body, active="", head=""):
    shop: Shop = req.app["shop"]
    s = await shop.settings()
    nav = render_nav(req, active, shop)
    flash = ""
    if req.query.get("ok"):
        flash += f"<div class='flash ok'>{e(req.query['ok'])}</div>"
    if req.query.get("err"):
        flash += f"<div class='flash err'>{e(req.query['err'])}</div>"
    shop_name = e(s.get("shop_name") or _("فروشگاه"))
    html = f"""<!doctype html><html lang="{e(req['lang'])}" dir="{'rtl' if req['rtl'] else 'ltr'}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{e(title)} · {shop_name}</title>
<style>{CSS}</style><script>{THEME_JS}</script>{head}</head><body><div class="shell">
<aside class="side" id="side"><button type="button" class="side-close" onclick="navToggle(false)" aria-label="close">✕</button><div class="brand"><i>★</i><span>{shop_name}</span></div>
<div class="nsearch"><input type="search" id="nq" placeholder="{e(_('جستجو در منو…'))}" autocomplete="off" oninput="ngFilter(this.value)"></div>
<nav class="nav-wrap" id="navw">{nav}</nav>
<div class="nfoot"><a class="nav" href="/logout"><span class="ni">{_I_LOGOUT}</span><span class="nt">{e(_('خروج'))}</span></a>{theme_ctrl()}</div></aside>
<div class="side-bd" onclick="navToggle(false)"></div>
<div class="main"><div class="top"><button type="button" class="burger" onclick="navToggle()">☰</button>
<h1>{nav_crumb(active)}{e(title)}</h1>{lang_switcher(req)}<span class="hint">{e(req['who'])}</span></div><div class="wrap">{flash}{body}</div></div></div>
<script>{BASE_JS}</script></body></html>"""
    return web.Response(text=html, content_type="text/html")


def lang_switcher(req):
    shop: Shop = req.app["shop"]
    langs = [l for l in shop.langs if l["web_enabled"]]
    if len(langs) < 2:
        return ""
    from urllib.parse import quote
    items = "".join(f"<a class='{'on' if l['code'] == req['lang'] else ''}' href='/lang/{e(l['code'])}?next={quote(req.path_qs)}'>{e(l['name'])}</a>" for l in langs)
    return f"<div class='langs'>{items}</div>"


# ---------------------------------------------------------------- نشست
def sig(secret, msg):
    return hmac.new(secret.encode(), msg.encode(), hashlib.sha256).hexdigest()


def make_session(secret, uid):
    exp = int(time.time()) + SESSION_TTL
    return f"{uid}.{exp}.{sig(secret, f'{uid}.{exp}')}"


def read_session(secret, value):
    try:
        uid, exp, sg = value.split(".")
        if int(exp) < time.time() or not hmac.compare_digest(sg, sig(secret, f"{uid}.{exp}")):
            return None
        return int(uid)
    except (ValueError, AttributeError):
        return None


def set_session(resp, shop: Shop, uid):
    resp.set_cookie("sid", make_session(shop.cfg.web_secret, uid), max_age=SESSION_TTL, httponly=True,
                    samesite="Strict", secure=shop.cfg.cookie_secure, path="/")


def actor(req):
    return "web:" + (str(req["uid"]) if req["uid"] else "panel")


def T(src, **kw):
    """ترجمه + escape برای قرار دادن در HTML."""
    return e(_(src, **kw))


def json_attr(v) -> str:
    return e(json.dumps(v, ensure_ascii=False))
