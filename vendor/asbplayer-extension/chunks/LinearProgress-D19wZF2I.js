import{in as e,on as t}from"./base64-4YujvIcm.js";import{Ct as n,Gt as r,Ht as i,J as a,Jt as o,Q as s,Tt as c,Wt as l,X as u,Y as d,Zt as f,_t as p,b as m,gt as h,it as g,k as _,o as v,wt as y,zt as b}from"./IconButton-DjqQ-WDY.js";import{t as x}from"./tab-registry-BDUw7mFp.js";var S=n(),C=v((0,S.jsx)(`path`,{d:`M4 9h4v11H4zm12 4h4v7h-4zm-6-9h4v16h-4z`}),`BarChart`),w=t(f(),1),T=new x(new _(new m)),E=e=>{let[t,n]=(0,w.useState)();return(0,w.useEffect)(()=>{let t=!0,r=async()=>{try{let r=await T.activeVideoElements(),i=e?.whereVideoElement,a=r.find(e=>e.synced&&e.loadedSubtitles&&(i===void 0||i?.(e))),o=e?.whereAsbplayer,s=await T.findAsbplayer({filter:e=>(e.loadedSubtitles&&(o===void 0||o?.(e)))??!1,allowTabCreation:!1});if(t){n(a?.src||s);return}}catch{}};r();let i=setInterval(()=>void r(),1e3);return()=>{t=!1,clearInterval(i)}},[e?.whereAsbplayer,e?.whereVideoElement]),t},D=async()=>{try{let t=await T.activeVideoElements(),n,r;for(let i of t)if(i.synced&&i.loadedSubtitles){let t=await e.tabs.get(i.id);(r===void 0||t.lastAccessed!==void 0&&r<t.lastAccessed)&&(n=i,r=t.lastAccessed)}let i=await T.asbplayerInstances(),a,o;for(let t of i)if(t.loadedSubtitles&&t.tabId!==void 0){let n=await e.tabs.get(t.tabId);(o===void 0||n.lastAccessed!==void 0&&o<n.lastAccessed)&&(a=t,o=n.lastAccessed)}return n===void 0?a?.id:a===void 0?n?.src:(r??0)<(o??0)?a.id:n.src}catch{}},O=()=>{let[e,t]=(0,w.useState)();return(0,w.useEffect)(()=>{let e=!0;return D().then(n=>{e&&t(n)}),()=>{e=!1}},[]),e},k=()=>{let[e,t]=(0,w.useState)();return(0,w.useEffect)(()=>{let e=!0,n=setInterval(()=>{D().then(n=>{e&&t(n)})},1e3);return()=>{e=!1,clearInterval(n)}},[]),e};function A(e){return p(`MuiLinearProgress`,e)}h(`MuiLinearProgress`,[`root`,`colorPrimary`,`colorSecondary`,`determinate`,`indeterminate`,`buffer`,`query`,`dashed`,`dashedColorPrimary`,`dashedColorSecondary`,`bar`,`bar1`,`bar2`,`barColorPrimary`,`barColorSecondary`,`bar1Indeterminate`,`bar1Determinate`,`bar1Buffer`,`bar2Indeterminate`,`bar2Buffer`]);var j=4,M=c`
  0% {
    left: -35%;
    right: 100%;
  }

  60% {
    left: 100%;
    right: -90%;
  }

  100% {
    left: 100%;
    right: -90%;
  }
`,N=typeof M==`string`?null:y`
        animation: ${M} 2.1s cubic-bezier(0.65, 0.815, 0.735, 0.395) infinite;
      `,P=c`
  0% {
    left: -200%;
    right: 100%;
  }

  60% {
    left: 107%;
    right: -8%;
  }

  100% {
    left: 107%;
    right: -8%;
  }
`,F=typeof P==`string`?null:y`
        animation: ${P} 2.1s cubic-bezier(0.165, 0.84, 0.44, 1) 1.15s infinite;
      `,I=c`
  0% {
    opacity: 1;
    background-position: 0 -23px;
  }

  60% {
    opacity: 0;
    background-position: 0 -23px;
  }

  100% {
    opacity: 1;
    background-position: -200px -23px;
  }
`,L=typeof I==`string`?null:y`
        animation: ${I} 3s infinite linear;
      `,R=e=>{let{classes:t,variant:n,color:r}=e;return i({root:[`root`,`color${b(r)}`,n],dashed:[`dashed`,`dashedColor${b(r)}`],bar1:[`bar`,`bar1`,`barColor${b(r)}`,(n===`indeterminate`||n===`query`)&&`bar1Indeterminate`,n===`determinate`&&`bar1Determinate`,n===`buffer`&&`bar1Buffer`],bar2:[`bar`,`bar2`,n!==`buffer`&&`barColor${b(r)}`,n===`buffer`&&`color${b(r)}`,(n===`indeterminate`||n===`query`)&&`bar2Indeterminate`,n===`buffer`&&`bar2Buffer`]},A,t)},z=(e,t)=>e.vars?e.vars.palette.LinearProgress[`${t}Bg`]:e.palette.mode===`light`?r(e.palette[t].main,.62):l(e.palette[t].main,.5),B=s(`span`,{name:`MuiLinearProgress`,slot:`Root`,overridesResolver:(e,t)=>{let{ownerState:n}=e;return[t.root,t[`color${b(n.color)}`],t[n.variant]]}})(u(({theme:e})=>({position:`relative`,overflow:`hidden`,display:`block`,height:4,zIndex:0,"@media print":{colorAdjust:`exact`},variants:[...Object.entries(e.palette).filter(d()).map(([t])=>({props:{color:t},style:{backgroundColor:z(e,t)}})),{props:({ownerState:e})=>e.color===`inherit`&&e.variant!==`buffer`,style:{"&::before":{content:`""`,position:`absolute`,left:0,top:0,right:0,bottom:0,backgroundColor:`currentColor`,opacity:.3}}},{props:{variant:`buffer`},style:{backgroundColor:`transparent`}},{props:{variant:`query`},style:{transform:`rotate(180deg)`}}]}))),V=s(`span`,{name:`MuiLinearProgress`,slot:`Dashed`,overridesResolver:(e,t)=>{let{ownerState:n}=e;return[t.dashed,t[`dashedColor${b(n.color)}`]]}})(u(({theme:e})=>({position:`absolute`,marginTop:0,height:`100%`,width:`100%`,backgroundSize:`10px 10px`,backgroundPosition:`0 -23px`,variants:[{props:{color:`inherit`},style:{opacity:.3,backgroundImage:`radial-gradient(currentColor 0%, currentColor 16%, transparent 42%)`}},...Object.entries(e.palette).filter(d()).map(([t])=>{let n=z(e,t);return{props:{color:t},style:{backgroundImage:`radial-gradient(${n} 0%, ${n} 16%, transparent 42%)`}}})]})),L||{animation:`${I} 3s infinite linear`}),H=s(`span`,{name:`MuiLinearProgress`,slot:`Bar1`,overridesResolver:(e,t)=>{let{ownerState:n}=e;return[t.bar,t.bar1,t[`barColor${b(n.color)}`],(n.variant===`indeterminate`||n.variant===`query`)&&t.bar1Indeterminate,n.variant===`determinate`&&t.bar1Determinate,n.variant===`buffer`&&t.bar1Buffer]}})(u(({theme:e})=>({width:`100%`,position:`absolute`,left:0,bottom:0,top:0,transition:`transform 0.2s linear`,transformOrigin:`left`,variants:[{props:{color:`inherit`},style:{backgroundColor:`currentColor`}},...Object.entries(e.palette).filter(d()).map(([t])=>({props:{color:t},style:{backgroundColor:(e.vars||e).palette[t].main}})),{props:{variant:`determinate`},style:{transition:`transform .${j}s linear`}},{props:{variant:`buffer`},style:{zIndex:1,transition:`transform .${j}s linear`}},{props:({ownerState:e})=>e.variant===`indeterminate`||e.variant===`query`,style:{width:`auto`}},{props:({ownerState:e})=>e.variant===`indeterminate`||e.variant===`query`,style:N||{animation:`${M} 2.1s cubic-bezier(0.65, 0.815, 0.735, 0.395) infinite`}}]}))),U=s(`span`,{name:`MuiLinearProgress`,slot:`Bar2`,overridesResolver:(e,t)=>{let{ownerState:n}=e;return[t.bar,t.bar2,t[`barColor${b(n.color)}`],(n.variant===`indeterminate`||n.variant===`query`)&&t.bar2Indeterminate,n.variant===`buffer`&&t.bar2Buffer]}})(u(({theme:e})=>({width:`100%`,position:`absolute`,left:0,bottom:0,top:0,transition:`transform 0.2s linear`,transformOrigin:`left`,variants:[...Object.entries(e.palette).filter(d()).map(([t])=>({props:{color:t},style:{"--LinearProgressBar2-barColor":(e.vars||e).palette[t].main}})),{props:({ownerState:e})=>e.variant!==`buffer`&&e.color!==`inherit`,style:{backgroundColor:`var(--LinearProgressBar2-barColor, currentColor)`}},{props:({ownerState:e})=>e.variant!==`buffer`&&e.color===`inherit`,style:{backgroundColor:`currentColor`}},{props:{color:`inherit`},style:{opacity:.3}},...Object.entries(e.palette).filter(d()).map(([t])=>({props:{color:t,variant:`buffer`},style:{backgroundColor:z(e,t),transition:`transform .${j}s linear`}})),{props:({ownerState:e})=>e.variant===`indeterminate`||e.variant===`query`,style:{width:`auto`}},{props:({ownerState:e})=>e.variant===`indeterminate`||e.variant===`query`,style:F||{animation:`${P} 2.1s cubic-bezier(0.165, 0.84, 0.44, 1) 1.15s infinite`}}]}))),W=w.forwardRef(function(e,t){let n=a({props:e,name:`MuiLinearProgress`}),{className:r,color:i=`primary`,value:s,valueBuffer:c,variant:l=`indeterminate`,...u}=n,d={...n,color:i,variant:l},f=R(d),p=g(),m={},h={bar1:{},bar2:{}};if((l===`determinate`||l===`buffer`)&&s!==void 0){m[`aria-valuenow`]=Math.round(s),m[`aria-valuemin`]=0,m[`aria-valuemax`]=100;let e=s-100;p&&(e=-e),h.bar1.transform=`translateX(${e}%)`}if(l===`buffer`&&c!==void 0){let e=(c||0)-100;p&&(e=-e),h.bar2.transform=`translateX(${e}%)`}return(0,S.jsxs)(B,{className:o(f.root,r),ownerState:d,role:`progressbar`,...m,ref:t,...u,children:[l===`buffer`?(0,S.jsx)(V,{className:f.dashed,ownerState:d}):null,(0,S.jsx)(H,{className:f.bar1,ownerState:d,style:h.bar1}),l===`determinate`?null:(0,S.jsx)(U,{className:f.bar2,ownerState:d,style:h.bar2})]})});export{E as a,O as i,T as n,C as o,k as r,W as t};