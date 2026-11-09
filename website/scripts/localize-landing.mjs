import fs from 'node:fs/promises';
import path from 'node:path';

export async function localizeLanding({site, dist, publicDir, root, md, shell}) {
 const dictionary=JSON.parse(await fs.readFile(path.join(site,'translations.en.json'),'utf8'));
 function translate(html) {
  return html.split(/(<[^>]*>)/g).map(part=>{
   if(part.startsWith('<'))return part.replace(/(aria-label|content|title)="([^"]*)"/g,(all,attr,value)=>dictionary[value]?`${attr}="${dictionary[value]}"`:all);
   const value=part.trim();return dictionary[value]?part.replace(value,dictionary[value]):part;
  }).join('').replace('lang="mk"','lang="en"');
 }
 const isLocalized=url=>url==='/' || /^\/(?:downloads\/[^/]+\.(?:html|md)|help\/|guides\/|404\.html)$/.test(url);
 function englishLinks(html) {return html.replace(/href="(\/[^"#]*)(#[^"]*)?"/g,(all,url,hash='')=>isLocalized(url)?`href="/en${url}${hash}"`:all).replace(/href="#/g,'href="#');}
 function switcher(relative,lang){
  const mk=relative==='index.html'?'/':'/'+relative.replace(/index\.html$/,'');
  const en='/en'+mk;
  return `<div class="language-switch" role="group" aria-label="${lang==='mk'?'Избери јазик':'Choose language'}"><a href="${mk}" data-language="mk" lang="mk" hreflang="mk" aria-label="Македонски"${lang==='mk'?' aria-current="page"':''}>МК</a><a href="${en}" data-language="en" lang="en" hreflang="en" aria-label="English"${lang==='en'?' aria-current="page"':''}>EN</a></div>`;
 }
 function decorate(html,relative,lang){
  const mk=relative==='index.html'?'/':'/'+relative.replace(/index\.html$/,'');
  return html.replace('</head>',`<link rel="alternate" hreflang="mk" href="${mk}"><link rel="alternate" hreflang="en" href="/en${mk}"><script src="/language.js" defer></script></head>`)
   .replace('</header>',switcher(relative,lang)+'</header>');
 }
 // Read the original English technical references before replacing Macedonian guide pages.
 async function walk(dir){const out=[];for(const e of await fs.readdir(dir,{withFileTypes:true})){if(e.name==='en')continue;const f=path.join(dir,e.name);if(e.isDirectory())out.push(...await walk(f));else if(e.name.endsWith('.html'))out.push(f)}return out;}
 const files=await walk(dist);
 await fs.mkdir(path.join(dist,'en/downloads'),{recursive:true});
 for(const file of await fs.readdir(path.join(dist,'downloads')))if(file.endsWith('.md'))await fs.copyFile(path.join(dist,'downloads',file),path.join(dist,'en/downloads',file));
 for(const file of files){
  const relative=path.relative(dist,file).split(path.sep).join('/');
  let original=await fs.readFile(file,'utf8');
  if(relative==='help/index.html'){
   original=original.replace('Брзиот почеток е на македонски. Деталните технички упатства подолу се на англиски и ја следат документацијата во проектот.','Изберете упатство за инсталација или користење. Практичните упатства се достапни на македонски и англиски; оригиналните детални технички референци се достапни на англиски.');
  }
  let english=translate(original);
  english=english.replace('Изберете упатство за инсталација или користење. Практичните упатства се достапни на македонски и англиски; оригиналните детални технички референци се достапни на англиски.','Choose an installation or usage guide below. Practical guides are available in Macedonian and English; original detailed technical references are available in English.');
  // Localize the sample data's number formatting as well as its labels.
  if(relative==='index.html')english=english.replaceAll('€124.580','€124,580').replaceAll('2.846','2,846').replaceAll('1.392','1,392').replaceAll('18,6%','18.6%').replaceAll('12,4%','12.4%').replaceAll('8,2%','8.2%');
  const output=path.join(dist,'en',relative);await fs.mkdir(path.dirname(output),{recursive:true});
  await fs.writeFile(output,decorate(englishLinks(english),relative,'en'));
  await fs.writeFile(file,decorate(original,relative,'mk'));
 }
 const installation=await fs.readFile(path.join(root,'INSTALLATION.md'),'utf8');
 const shared=installation.split('## 5. Initialize on Linux or macOS')[1].split('## 6.')[0];
 const macCommands=[...shared.matchAll(/```bash\r?\n([\s\S]*?)```/g)].map(m=>m[1].trim()).join('\n\n');
 const clone='git clone https://github.com/SlavchoSpasenoski/Bi-Dash.git\ncd Bi-Dash';
 const commands={windows:clone+'\npwsh -NoProfile -File ./scripts/start-local.ps1',macos:clone+'\n\n'+macCommands,linux:clone+'\nbash scripts/linux.sh install'};
 for(const filename of await fs.readdir(path.join(site,'guides/mk'))){
  let source=await fs.readFile(path.join(site,'guides/mk',filename),'utf8');
  source=source.replace(/\{\{(windows|macos|linux)\}\}/g,(_,key)=>commands[key]);
  const content=(await md.render(source)).code;
  const relative='downloads/'+filename.replace('.md','.html');
  const title=source.match(/^# (.+)/m)?.[1]||'Упатство';
  await fs.writeFile(path.join(dist,relative),decorate(shell(title,content),relative,'mk'));
  await fs.writeFile(path.join(dist,'downloads',filename),source);
 }
 // Label untranslated project/legal background documents as original English references.
 const localized=new Set((await fs.readdir(path.join(site,'guides/mk'))).map(x=>x.replace('.md','.html')));
 for(const file of files.filter(f=>path.dirname(f)===path.join(dist,'downloads')&&!localized.has(path.basename(f))&&!/^(windows|macos|linux)\.html$/.test(path.basename(f)))){
  let html=await fs.readFile(file,'utf8');
  html=html.replace('<main class="wrap doc">','<main class="wrap doc"><p class="reference-note">Оригинална техничка референца на англиски. <a href="/guides/">Практични упатства на македонски →</a></p><article lang="en">').replace('</main>','</article></main>');
  await fs.writeFile(file,html);
 }
 // Shared guide hub for both Astro and the standalone distribution.
 for(const lang of ['mk','en']){
  const base=lang==='en'?path.join(dist,'en'):dist;
  const hub=await fs.readFile(path.join(base,'help/index.html'),'utf8');
  await fs.mkdir(path.join(base,'guides'),{recursive:true});
  await fs.writeFile(path.join(base,'guides/index.html'),hub.replaceAll('href="/help/"','href="/guides/"').replaceAll('href="/en/help/"','href="/en/guides/"'));
 }
 await fs.writeFile(path.join(site,'mk.html'),await fs.readFile(path.join(dist,'index.html')));
 await fs.writeFile(path.join(site,'en.html'),await fs.readFile(path.join(dist,'en/index.html')));
 // Keep language routes and downloads available when building the main Astro site.
 for(const folder of ['downloads','guides','en/downloads','en/help','en/guides']){
  await fs.mkdir(path.join(publicDir,folder),{recursive:true});
  await fs.cp(path.join(dist,folder),path.join(publicDir,folder),{recursive:true});
 }
 await fs.copyFile(path.join(publicDir,'language.js'),path.join(dist,'language.js'));
 console.log('Built MK/EN routes, language navigation and 6 Macedonian practical guides.');
}
