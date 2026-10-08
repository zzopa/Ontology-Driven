"use client";
import { useCallback, useEffect, useState } from "react";
import { Palette } from "lucide-react";
import { Button, Modal } from "./ui";
const themes = [
 ["曜石绿", "#0c0c0e", "#ededf0", "#10b981"],
 ["极简白", "#f4f6f8", "#202938", "#047857"],
 ["深海蓝", "#0b1425", "#e2edff", "#38bdf8"],
 ["星夜紫", "#181225", "#ede5ff", "#a78bfa"],
 ["森林绿", "#101e18", "#e0f2e7", "#4ade80"],
 ["樱花粉", "#fff1f4", "#4a2535", "#be185d"],
 ["暖沙金", "#f7f1e5", "#44382b", "#a16207"],
 ["雾灰蓝", "#eaf0f5", "#263748", "#0369a1"],
];
type Settings = { theme: number; bg: string; text: string };
const initial: Settings = { theme: 0, bg: themes[0][1], text: themes[0][2] };
const key = "hbask-appearance-v1";
const valid = (v: unknown): v is string => typeof v === "string" && /^#[a-f\d]{6}$/i.test(v);
function mix(a: string, b: string, ratio: number) {
 return "#" + [1,3,5].map(i => Math.round(parseInt(a.slice(i,i+2),16)*(1-ratio)+parseInt(b.slice(i,i+2),16)*ratio).toString(16).padStart(2,"0")).join("");
}
function apply(s: Settings) {
 const root=document.documentElement;
 root.style.colorScheme=[1,3,5].reduce((sum,i)=>sum+parseInt(s.bg.slice(i,i+2),16),0)>420?"light":"dark";
 const accent=themes[s.theme][3];
 const tokens={bg:s.bg,text:s.text,panel:mix(s.bg,s.text,.035),surface:mix(s.bg,s.text,.07),line:mix(s.bg,s.text,.17),muted:mix(s.bg,s.text,.66),subtle:mix(s.bg,s.text,.49),accent,"accent-bright":accent};
 for(const [k,v] of Object.entries(tokens)) root.style.setProperty(`--${k}`,v);
}
export function ThemeSettings() {
 const [settings,setSettings]=useState(initial),[open,setOpen]=useState(false),[error,setError]=useState(false);
 const close=useCallback(()=>setOpen(false),[]);
 useEffect(()=>{
  function restore(){let s=initial;try{const v=JSON.parse(localStorage.getItem(key)||"null");if(v&&Number.isInteger(v.theme)&&themes[v.theme]&&valid(v.bg)&&valid(v.text))s={theme:v.theme,bg:v.bg,text:v.text};}catch{}setSettings(s);apply(s);}
  restore();const sync=(e:StorageEvent)=>{if(e.key===key||e.key===null)restore();};window.addEventListener("storage",sync);return()=>window.removeEventListener("storage",sync);
 },[]);
 function update(s:Settings){setSettings(s);apply(s);try{localStorage.setItem(key,JSON.stringify(s));setError(false);}catch{setError(true);}}
 return <><Button aria-label="主题与颜色设置" onClick={()=>setOpen(true)}><Palette size={14}/>主题</Button>{open&&<Modal title="主题与颜色" subtitle="即时预览，自动保存到当前浏览器" onClose={close}>
 <div className="theme-grid">{themes.map((t,i)=><button className="theme-option" key={t[0]} aria-pressed={settings.theme===i} onClick={()=>update({theme:i,bg:t[1],text:t[2]})}><span className="theme-swatch" style={{background:t[1],border:`1px solid ${t[3]}`,color:t[2]}}>Aa</span>{t[0]}</button>)}</div>
 <div className="theme-colors">{(["bg","text"] as const).map(k=><label key={k}>{k==="bg"?"背景颜色":"字体颜色"}<input type="color" aria-label={k==="bg"?"背景颜色选择器":"字体颜色选择器"} value={settings[k]} onChange={e=>update({...settings,[k]:e.target.value})}/><input key={settings[k]} aria-label={k==="bg"?"背景颜色十六进制值":"字体颜色十六进制值"} defaultValue={settings[k]} maxLength={7} onBlur={e=>{if(valid(e.target.value))update({...settings,[k]:e.target.value});else e.target.value=settings[k];}} onKeyDown={e=>{if(e.key==="Enter"&&!e.nativeEvent.isComposing&&e.nativeEvent.keyCode!==229){e.preventDefault();e.currentTarget.blur();}}}/></label>)}</div>
 <p className="helper">支持 #RRGGBB。切换预设会恢复该主题的配色。</p>{error&&<p role="status">当前浏览器无法保存设置，配色仅在本次页面有效。</p>}
 <div className="modal-actions"><Button onClick={()=>update(initial)}>恢复默认</Button><Button enterConfirm variant="primary" onClick={close}>完成</Button></div>
 </Modal>}</>;
}
