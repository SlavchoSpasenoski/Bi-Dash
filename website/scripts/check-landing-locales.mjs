import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'../bi-dash-site/dist');
function read(p){return fs.readFileSync(path.join(root,p),'utf8')}
const mk=read('index.html'),en=read('en/index.html');
assert(mk.includes('<html lang="mk">'));assert(en.includes('<html lang="en">'));
assert(mk.includes('Вашите податоци.'));assert(en.includes('Your data.'));
assert.equal((mk.match(/class="language-switch"/g)||[]).length,1);
assert.equal((en.match(/class="language-switch"/g)||[]).length,1);
assert(!/[\u0400-\u04ff]/.test(en.replace(/<[^>]+>/g,'').replaceAll('МК','')),'English homepage contains untranslated text');
for(const os of ['windows','macos','linux']){
 assert(read('downloads/'+os+'.html').includes('Започнете на'));
 assert(read('en/downloads/'+os+'.html').includes('Get started on'));
 assert(en.includes('href="/en/downloads/'+os+'.html"'));
}
for(const name of ['INSTALLATION','USER_GUIDE','LINUX_DEPLOYMENT','AI_DASHBOARDS','ACCESS_CONTROL','LOCAL_PROJECT']){
 const page=read('downloads/'+name+'.html');assert(page.includes('lang="mk"'));assert(page.includes('/en/downloads/'+name+'.html'));
 assert(read('en/downloads/'+name+'.html').includes('lang="en"'));
 assert(!read('en/downloads/'+name+'.md').includes('{{'));
}
function walk(dir){return fs.readdirSync(dir,{withFileTypes:true}).flatMap(e=>e.isDirectory()?walk(path.join(dir,e.name)):[path.join(dir,e.name)])}
let references=0;
for(const file of walk(root).filter(f=>f.endsWith('.html'))){const html=fs.readFileSync(file,'utf8');for(const match of html.matchAll(/(?:href|src)="(\/[^"#]*)(?:#[^"]*)?"/g)){let target=path.join(root,match[1]);if(fs.existsSync(target)&&fs.statSync(target).isDirectory())target=path.join(target,'index.html');assert(fs.existsSync(target),path.relative(root,file)+' -> '+match[1]);references++}}
console.log(`Passed: MK/EN homepages, 6 practical guide pairs, 3 download pairs and ${references} local links.`);
