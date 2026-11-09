import { localizeLanding } from './localize-landing.mjs';
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createMarkdownProcessor } from '../node_modules/@astrojs/markdown-remark/dist/index.js';
const site=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'../bi-dash-site');
const root=path.resolve(site,'../..');
const dist=path.join(site,'dist');
const publicDir=path.resolve(site,'../public');
const md=await createMarkdownProcessor({syntaxHighlight:false});
const escape=s=>s.replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;');
await fs.mkdir(path.join(dist,'downloads'),{recursive:true});
await fs.mkdir(path.join(dist,'help'),{recursive:true});
await fs.mkdir(path.join(dist,'assets/images'),{recursive:true});
for(const file of ['landing.css','landing.js','LICENSE.txt','NOTICE.txt'])await fs.copyFile(path.join(publicDir,file),path.join(dist,file));
for(const file of ['bi-dash-mark.svg','bi-dash-wordmark.svg'])await fs.copyFile(path.join(publicDir,'assets/images',file),path.join(dist,'assets/images',file));
await fs.copyFile(path.join(site,'index.html'),path.join(dist,'index.html'));
const docs=['INSTALLATION.md','USER_GUIDE.md','LINUX_DEPLOYMENT.md','AI_DASHBOARDS.md','AI_DASHBOARDS.md','ACCESS_CONTROL.md','LOCAL_PROJECT.md','BRANDING.md','README.md','COPYRIGHT.md','NOTICE','LICENSE','README.upstream.md','CONTRIBUTING.md','SECURITY.md','USER_GUIDE.mk.md'];
function shell(title,content){return `<!doctype html><html lang="mk"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>${escape(title)} · Bi-Dash</title><link rel="icon" href="/assets/images/bi-dash-mark.svg"><link rel="stylesheet" href="/landing.css"><style>.doc{max-width:900px;padding:55px 0 90px}.doc h1{font-size:38px;margin:30px 0}.doc h2{font-size:27px;margin:40px 0 18px}.doc h3{font-size:21px;margin:28px 0 14px}.doc p{margin:16px 0}.doc a{color:#2563eb;text-decoration:underline}.doc pre{background:#191e29;border-radius:6px;padding:22px}.doc code{overflow-wrap:anywhere}.doc p code,.doc li code{background:#eef2f8;color:#273e66;padding:2px 5px}.doc table{display:block;overflow:auto;border-collapse:collapse;font-size:13px}.doc td,.doc th{border:1px solid #dce2ec;padding:10px;text-align:left}.doc li{margin-bottom:8px}.doc img{max-width:100%}.doc blockquote{border-left:3px solid #2563eb;margin:20px 0;padding:10px 20px;background:#f4f6fa}</style></head><body><header><div class="wrap nav"><a href="/"><img src="/assets/images/bi-dash-wordmark.svg" width="152" height="40" alt="Bi-Dash"></a><a href="/#download">← Преземања</a></div></header><main class="wrap doc">${content}</main></body></html>`}
for(const file of new Set(docs)){
 let text=await fs.readFile(path.join(root,file),'utf8');
 await fs.writeFile(path.join(dist,'downloads',file),text);
 if(file.endsWith('.md')){
 text=text.replace(/\]\((?!https?:|#)([^)]+)\)/g,(all,target)=>{if(target.startsWith('docs/'))return `](https://github.com/SlavchoSpasenoski/Bi-Dash/blob/main/${target})`;const [base,anchor]=target.split('#');if(docs.includes(base))return `](/downloads/${base.replace(/\.md$/,'.html')}${anchor?'#'+anchor:''})`;return `](https://github.com/SlavchoSpasenoski/Bi-Dash/blob/main/${target})`});
 const rendered=await md.render(text);
 await fs.writeFile(path.join(dist,'downloads',file.replace('.md','.html')),shell(file.replace('.md',''),rendered.code));
 }
}
const zip='https://github.com/SlavchoSpasenoski/Bi-Dash/archive/refs/heads/main.zip';
for(const [os,label,prereq,guide] of [['windows','Windows','Docker Desktop со Linux containers, Git и PowerShell 7','INSTALLATION'],['macos','macOS','Docker Desktop, Git и OpenSSL. Проверете ја архитектурата на вашиот Mac','INSTALLATION'],['linux','Linux','Docker Engine, Compose v2, Git, Bash и OpenSSL. Серверската постапка е за x86-64','LINUX_DEPLOYMENT']]){
 await fs.writeFile(path.join(dist,'downloads',os+'.html'),shell('Преземи за '+label,`<p class="eyebrow">BI-DASH / ${label.toUpperCase()}</p><h1>Започнете на ${label}.</h1><p>Bi-Dash се презема како изворен код и се инсталира преку Docker. ZIP-архивата е иста за сите системи; постапката за инсталација е различна.</p><p><strong>Предуслови:</strong> ${prereq}.</p><p><a class="button" style="color:white;text-decoration:none" href="${zip}">Преземи Bi-Dash ZIP ↓</a></p><p>Отпакувајте ја архивата и отворете терминал во папката Bi-Dash-main. Потоа следете го упатството од чекорот по преземањето, или користете git clone според упатството.</p><p><a href="/downloads/${guide}.html">Отвори целосно упатство →</a> · <a href="/downloads/${guide}.md" download>Зачувај упатство ↓</a></p><p>За свежа база употребете ја иницијализацијата само еднаш. На постоечка инсталација следете ги чекорите за стартување или надградба.</p><p class="fine">Windows со Linux контејнери е проверен. Целосна свежа инсталација на физички Linux/macOS хост и ARM компатибилноста сè уште бараат валидација.</p><p><a href="https://github.com/SlavchoSpasenoski/Bi-Dash">Изворен код и историја на GitHub ↗</a></p>`));
}
await fs.writeFile(path.join(dist,'help/index.html'),shell('Документација',`<p class="eyebrow">BI-DASH / ДОКУМЕНТАЦИЈА</p><h1>Од прва инсталација<br>до прв извештај.</h1><p>Брзиот почеток е на македонски. Деталните технички упатства подолу се на англиски и ја следат документацијата во проектот.</p><div class="resources"><a href="/downloads/INSTALLATION.html"><b>Инсталација</b><small>Windows, macOS и Linux</small></a><a href="/downloads/USER_GUIDE.html"><b>Корисничко упатство</b><small>Прашања, графикони и контролни табли</small></a><a href="/downloads/LINUX_DEPLOYMENT.html"><b>Linux сервер</b><small>Инсталација и операции</small></a><a href="/downloads/AI_DASHBOARDS.html"><b>AI контролни табли</b><small>Нацрти од текстуален опис</small></a><a href="/downloads/ACCESS_CONTROL.html"><b>Улоги и пристап</b><small>Admin, Editor и Reader</small></a><a href="/downloads/LOCAL_PROJECT.html"><b>Локална околина</b><small>Конфигурација и валидација</small></a></div>`));
await fs.writeFile(path.join(dist,'404.html'),shell('Страницата не е пронајдена','<h1>Оваа страница не постои.</h1><p><a href="/">Вратете се на почетната страница →</a></p>'));
console.log('Built static site with 3 OS download pages, rendered documentation and local guide copies.');

await localizeLanding({site,dist,publicDir,root,md,shell});
