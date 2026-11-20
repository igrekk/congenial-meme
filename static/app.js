import {monthStart,shiftMonth,calendarRange,monthEntries,projectTotals,formatHours} from './calendar.js';

const root = document.getElementById('tt-worktime');
const q = s => root.querySelector(s);
const qa = s => [...root.querySelectorAll(s)];
const projects = ['INAL','INAC','GCVU','ECGP'];
const escape = value => String(value ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const parseDate = value => new Date(value+'T12:00:00Z');
const addDay = (value, count) => {const d=parseDate(value);d.setUTCDate(d.getUTCDate()+count);return d.toISOString().slice(0,10);};
const isDate = value => /^\d{4}-\d{2}-\d{2}$/.test(value) && Number.isFinite(parseDate(value).getTime()) && parseDate(value).toISOString().slice(0,10)===value;
const sum = values => values.reduce((n,item)=>n+item.duration_seconds,0);
const clock = value => {const s=Math.max(0,Math.floor(value));return [Math.floor(s/3600),Math.floor(s%3600/60),s%60].map(n=>String(n).padStart(2,'0')).join(':');};
const format = value => {const n=Math.max(0,Math.floor(value)),h=Math.floor(n/3600),m=Math.floor(n%3600/60),s=n%60;return h?h+' ч'+(m?' '+m+' мин':'')+(s?' '+s+' с':''):m?m+' мин'+(s?' '+s+' с':''):s+' с';};
const uid = () => crypto.randomUUID();
const task = id => data.tasks.find(t=>t.id===id);
const normalize = text => text.trim().toLocaleLowerCase('ru');
let data={projects:[],tasks:[],entries:[],timer:null,today:null,server_now:0};
let ui={view:'journal',mode:'manual',day:null,month:null,filter:'all',editing:null,showArchive:false,composerOpen:false};
let ready=false, connected=false, busy=false, timerReceivedAt=0, requestSerial=0, requestController=null;
let removedId=null, renameId=null, restoreName=null, pendingEntry=null;
let backupList=[];
let connectionError=false;

function parseDuration(value){
  const v=value.trim().replace(',','.');let seconds;
  if(/^\d{1,2}:\d{2}(:\d{2})?$/.test(v)){
    const a=v.split(':').map(Number);if(a[1]>59||(a[2]||0)>59)return NaN;
    seconds=a[0]*3600+a[1]*60+(a[2]||0);
  }else if(/^\d+(\.\d{1,5})?$/.test(v))seconds=Math.round(Number(v)*3600);
  else return NaN;
  return Number.isInteger(seconds)&&seconds>0&&seconds<=86400?seconds:NaN;
}
function showError(message){q('[data-error]').textContent=message;q('[data-error]').hidden=!message;}
function notice(message){q('[data-notice]').textContent=message;}
function dot(project){return '<span class="tt-dot tt-color-'+escape(project)+'" aria-hidden="true"></span>';}
function actionStates(){
  root.setAttribute('aria-busy',String(busy));
  qa('[data-mutate]').forEach(b=>{b.disabled=busy||!ready||!connected||(!!b.dataset.run&&!!data.timer);});
  qa('[data-start]').forEach(b=>{b.disabled=busy||!ready||!connected||!!data.timer;});
  qa('[data-backup-restore]').forEach(b=>{b.disabled=busy||!ready||!connected||!!data.timer;});
  q('[data-connection]').textContent=!ready?'Подключение…':!connected?'Нет связи':busy?'Сохранение…':'Локально · сохранено';
  q('[data-connection]').classList.toggle('tt-offline',ready&&!connected);
}
async function api(path,method='GET',payload){
  const controller=new AbortController();const timeout=setTimeout(()=>controller.abort(),15000);
  try{
    const response=await fetch(path,{method,cache:'no-store',signal:controller.signal,headers:method==='GET'?{}:{'Content-Type':'application/json','X-Time-Tracker':'1'},...(method==='GET'?{}:{body:JSON.stringify(payload??{})})});
    let value;try{value=await response.json();}catch{throw new Error('Сервер вернул неожиданный ответ.');}
    if(!response.ok)throw new Error(value.error||'Не удалось выполнить действие.');
    return value;
  }catch(error){if(error.name==='AbortError')throw new Error('Сервер не ответил. Проверьте, что приложение запущено.');throw error;}
  finally{clearTimeout(timeout);}
}
async function refresh({initial=false}={}){
  const serial=++requestSerial;requestController?.abort();requestController=new AbortController();
  const controller=requestController;const timeout=setTimeout(()=>controller.abort(),12000);
  const query=ui.month?(()=>{const range=calendarRange(ui.month);return '?from='+range.from+'&to='+range.to;})():'';
  try{
    const read=async query=>{const response=await fetch('/api/state'+query,{cache:'no-store',signal:controller.signal});const value=await response.json();if(!response.ok)throw new Error(value.error||'Не удалось загрузить данные.');return value;};
    let value=await read(query);
    if(serial!==requestSerial)return false;
    if(!ui.month){
      ui.day=value.today;ui.month=monthStart(value.today);q('[data-date]').value=ui.day;
      if(value.timer)ui.mode='timer';
      const range=calendarRange(ui.month);value=await read('?from='+range.from+'&to='+range.to);
    }
    if(serial!==requestSerial)return false;
    data=value;timerReceivedAt=Date.now();connected=true;ready=true;
    if(connectionError){showError('');connectionError=false;}
    render();return true;
  }catch(error){
    if(serial!==requestSerial||error.name==='AbortError'&&controller!==requestController)return false;
    connected=false;connectionError=true;showError(!ready?'Не удалось подключиться к приложению. Убедитесь, что локальная служба запущена.':'Нет связи с приложением. Изменения пока не сохранены. Проверьте, что локальная служба запущена.');actionStates();return false;
  }finally{clearTimeout(timeout);}
}
async function perform(operation,onSuccess,message){
  if(busy||!ready||!connected)return;
  busy=true;showError('');actionStates();
  requestSerial++;requestController?.abort();
  try{
    const value=await operation();onSuccess?.(value);
    await refresh();
    if(message)notice(message);
  }catch(error){showError(error.message||'Не удалось сохранить изменения.');}
  finally{busy=false;actionStates();}
}
function updateOptions(){
  q('#tt-task-options').innerHTML=data.tasks.filter(t=>!t.archived&&t.project===q('[data-project]').value).map(t=>'<option value="'+escape(t.title)+'"></option>').join('');
}
function renderComposer(){
  q('[data-entry-form]').hidden=!ui.composerOpen;
  q('[data-toggle-composer]').setAttribute('aria-expanded',String(ui.composerOpen));
  q('[data-toggle-composer]').textContent=ui.composerOpen?'Скрыть ввод':'＋ Внести время';
  q('[data-toggle-composer]').hidden=ui.view!=='journal';
  qa('[data-mode]').forEach(b=>b.setAttribute('aria-pressed',String(ui.mode===b.dataset.mode)));
  q('[data-manual-fields]').hidden=ui.mode!=='manual';q('[data-note-field]').hidden=ui.mode!=='manual';q('[data-timer-fields]').hidden=ui.mode!=='timer';
  q('[data-cancel]').hidden=!ui.editing;q('[data-delete]').hidden=!ui.editing;q('[data-save]').textContent=ui.editing?'Сохранить изменения':'Добавить запись';
  q('[data-timer-idle]').hidden=!!data.timer;q('[data-timer-active]').hidden=!data.timer;q('[data-composer-fields]').hidden=!!data.timer&&ui.mode==='timer';
  q('[data-return-timer]').hidden=!data.timer;
  if(data.timer){
    const t=task(data.timer.task_id);
    q('[data-active-task]').textContent=t?t.project+' · '+t.title:'Задача таймера';
    q('[data-active-status]').textContent=data.timer.status==='paused'?'На паузе · время не учитывается':'Идет работа · запись создастся после завершения';
    q('[data-pause]').textContent=data.timer.status==='paused'?'Продолжить':'Пауза';
  }
  updateOptions();tick();
}
function filteredEntries(){return data.entries.filter(e=>ui.filter==='all'||task(e.task_id)?.project===ui.filter);}
function renderCalendar(){
  if(!ui.month)return;
  const range=calendarRange(ui.month),entries=filteredEntries();
  const monthName=new Intl.DateTimeFormat('ru',{month:'long',year:'numeric',timeZone:'UTC'}).format(parseDate(ui.month));
  q('[data-range]').textContent=monthName[0].toLocaleUpperCase('ru')+monthName.slice(1);
  q('[data-month-total]').textContent=format(sum(monthEntries(entries,ui.month)));
  q('[data-month-total-label]').textContent=(ui.filter==='all'?'Все проекты':ui.filter)+' · за месяц';
  q('[data-filters]').innerHTML=['all',...projects].map(p=>'<button type="button" data-filter="'+p+'" aria-pressed="'+(p===ui.filter)+'">'+(p==='all'?'Все проекты':dot(p)+p)+'</button>').join('');
  q('[data-days]').innerHTML=range.days.map(date=>{
    const items=entries.filter(e=>e.work_date===date),totals=projectTotals(items,data.tasks),total=sum(items),d=parseDate(date),inMonth=date.slice(0,7)===ui.month.slice(0,7);
    const description=projects.filter(p=>totals[p]>0).map(p=>p+': '+format(totals[p])).join(', ');
    const label=new Intl.DateTimeFormat('ru',{weekday:'long',day:'numeric',month:'long',year:'numeric',timeZone:'UTC'}).format(d)+', '+(description||'нет записей');
    const rows=projects.filter(p=>totals[p]>0).map(p=>'<span class="tt-calendar-project" data-calendar-project="'+p+'" title="'+p+' · '+escape(format(totals[p]))+'"><span class="tt-calendar-project-code">'+dot(p)+p+'</span><span class="tt-calendar-project-hours">'+escape(formatHours(totals[p]))+'</span></span>').join('');
    return '<button class="tt-calendar-day'+(!inMonth?' tt-other-month':'')+(date===data.today?' tt-today':'')+'" type="button" data-day="'+date+'" aria-pressed="'+(date===ui.day)+'"'+(date===data.today?' aria-current="date"':'')+' aria-label="'+escape(label)+'"><span class="tt-calendar-date"><span class="tt-calendar-number">'+d.getUTCDate()+'</span>'+(date===data.today?'<span class="tt-today-label">Сегодня</span>':'')+'</span><span class="tt-calendar-projects">'+(rows||'<span class="tt-calendar-empty" aria-hidden="true">—</span>')+'</span>'+(total?'<span class="tt-calendar-day-total">'+escape(formatHours(total))+'</span>':'')+'</button>';
  }).join('');
  const totals=projectTotals(monthEntries(data.entries,ui.month),data.tasks);
  q('[data-project-summary]').innerHTML=projects.map(p=>'<div class="tt-project-line"><span class="tt-project-tag">'+dot(p)+p+'</span><span class="tt-project-value">'+format(totals[p])+'</span></div>').join('');
}
function renderEntries(){
  if(!ui.day)return;
  q('[data-day-title]').textContent=(ui.day===data.today?'Сегодня, ':'')+new Intl.DateTimeFormat('ru',{day:'numeric',month:'long',year:'numeric',timeZone:'UTC'}).format(parseDate(ui.day));
  const entries=filteredEntries().filter(e=>e.work_date===ui.day);
  const count=entries.length,noun=count%10===1&&count%100!==11?'запись':count%10>=2&&count%10<=4&&!(count%100>=12&&count%100<=14)?'записи':'записей';
  q('[data-day-total]').textContent=(ui.filter==='all'?'Все проекты':ui.filter)+' · '+count+' '+noun+' · '+format(sum(entries));
  q('[data-day-warning]').hidden=sum(data.entries.filter(e=>e.work_date===ui.day))<=86400;
  q('[data-detail-filter]').hidden=ui.filter==='all';
  q('[data-detail-filter-label]').textContent='Показаны записи проекта '+ui.filter;
  q('[data-entries]').innerHTML=entries.length?entries.map(e=>{
    const t=task(e.task_id),id=escape(e.id),title=t?.title||'Задача',project=t?.project||'INAL';
    return '<article class="tt-entry"><div><p class="tt-entry-title">'+escape(title)+'</p><div class="tt-meta"><span class="tt-project-tag">'+dot(project)+escape(project)+'</span><span>·</span><span>'+(e.source==='timer'?'По таймеру':'Вручную')+'</span>'+(t?.archived?'<span>· В архиве</span>':'')+'</div>'+(e.note?'<p class="tt-entry-note tt-small tt-muted">'+escape(e.note)+'</p>':'')+'</div><span class="tt-entry-duration">'+format(e.duration_seconds)+'</span><div class="tt-entry-actions"><button class="tt-icon-button" type="button" data-edit="'+id+'" aria-label="Изменить запись: '+escape(title)+'">✎</button>'+(t&&!t.archived?'<button class="tt-icon-button" type="button" data-run="'+escape(t.id)+'" data-mutate aria-label="Запустить таймер: '+escape(title)+'" '+(data.timer?'disabled':'')+'>▶</button>':'')+'</div></article>';
  }).join(''):'<div class="tt-empty"><strong>Записей пока нет</strong><br>Добавьте часы или запустите таймер.<br><span class="tt-small">Ручная запись требует только дату и длительность.</span></div>';
}
function renderTasks(){
  const tasks=data.tasks.filter(t=>ui.showArchive||!t.archived);
  q('[data-task-list]').innerHTML=tasks.length?tasks.map(t=>'<article class="tt-task-row'+(t.archived?' tt-archived':'')+'"><div><p class="tt-entry-title">'+escape(t.title)+'</p><span class="tt-project-tag tt-small">'+dot(t.project)+escape(t.project)+'</span>'+(t.archived?'<span class="tt-small tt-muted"> · В архиве</span>':'')+'</div><span class="tt-project-value">'+format(t.total_seconds||0)+'</span><div class="tt-task-actions">'+(!t.archived?'<button class="tt-button" type="button" data-run="'+escape(t.id)+'" data-mutate '+(data.timer?'disabled':'')+'>▶ Начать</button>':'')+'<button class="tt-button tt-quiet" type="button" data-rename="'+escape(t.id)+'">Изменить</button><button class="tt-button tt-quiet" type="button" data-archive="'+escape(t.id)+'" data-mutate>'+(t.archived?'Вернуть':'В архив')+'</button></div></article>').join(''):'<div class="tt-empty"><strong>Создайте первую задачу</strong><br>Выберите проект и напишите, над чем будете работать.</div>';
}
function renderBackups(){
  q('[data-backups]').innerHTML=backupList.length?backupList.map(b=>{
    const name=escape(b.name),size=Number(b.size||0),date=b.created_at?new Date(typeof b.created_at==='number'?b.created_at*1000:b.created_at):null;
    const label=date&&Number.isFinite(date.getTime())?date.toLocaleString('ru-RU'):b.name;
    return '<article class="tt-backup-row"><div><p class="tt-entry-title">'+escape(label)+'</p><p class="tt-small tt-muted">'+name+(size?' · '+Math.max(1,Math.ceil(size/1024))+' КБ':'')+'</p></div><div class="tt-task-actions"><a class="tt-button" href="/api/backups/'+encodeURIComponent(b.name)+'/download" download>Скачать</a><button class="tt-button" type="button" data-backup-restore="'+name+'" '+(data.timer?'disabled':'')+'>Восстановить</button></div></article>';
  }).join(''):'<div class="tt-empty">Резервных копий пока нет.</div>';
}
async function loadBackups(){
  try{const value=await api('/api/backups');backupList=Array.isArray(value)?value:value.backups||[];renderBackups();actionStates();}
  catch(error){showError(error.message);}
}
function render(){
  q('[data-journal]').hidden=ui.view!=='journal';q('[data-tasks-view]').hidden=ui.view!=='tasks';q('[data-data-view]').hidden=ui.view!=='data';
  q('[data-page-title]').textContent={journal:'Рабочий календарь',tasks:'Задачи и проекты',data:'Ваши данные'}[ui.view];
  qa('[data-view]').forEach(b=>{if(b.dataset.view===ui.view)b.setAttribute('aria-current','page');else b.removeAttribute('aria-current');});
  renderComposer();renderCalendar();renderEntries();renderTasks();renderBackups();actionStates();
}
function elapsed(){return data.timer?Math.max(0,Math.floor(data.timer.elapsed_seconds+(data.timer.status==='running'?(Date.now()-timerReceivedAt)/1000:0))):0;}
function tick(){if(data.timer){const text=clock(elapsed());q('[data-elapsed]').textContent=text;q('[data-global-clock]').textContent=(data.timer.status==='paused'?'Пауза · ':'◷ ')+text;}}
function clearEditing({clearTask=true}={}){
  ui.editing=null;q('[data-duration]').value='';q('[data-note]').value='';if(clearTask)q('[data-task]').value='';pendingEntry=null;renderComposer();
}
function selectedDay(date){if(ui.editing)clearEditing();ui.day=date;q('[data-date]').value=date;renderCalendar();renderEntries();actionStates();}
function closeDayDetails(){if(q('[data-day-dialog]').open)q('[data-day-dialog]').close();}
function openComposer(mode='manual'){
  closeDayDetails();ui.view='journal';ui.mode=mode;ui.composerOpen=true;
  render();q('[data-entry-form]').scrollIntoView({block:'nearest'});
  if(mode==='manual')q('[data-task]').focus();
}
async function saveEntry(){
  if(busy||ui.mode!=='manual')return;
  const title=q('[data-task]').value.trim(),date=q('[data-date]').value,seconds=parseDuration(q('[data-duration]').value);
  if(!title)return showError('Укажите задачу, над которой работали.');
  if(!isDate(date))return showError('Укажите корректную дату.');
  if(!Number.isFinite(seconds))return showError('Введите длительность от 1 секунды до 24 часов: например 2,5 или 02:30.');
  const payload={project:q('[data-project]').value,title,work_date:date,duration_seconds:seconds,note:q('[data-note]').value.trim()};
  if(ui.editing){const original=data.entries.find(e=>e.id===ui.editing),originalTask=task(original?.task_id);if(originalTask&&originalTask.project===payload.project&&originalTask.title===payload.title)payload.task_id=originalTask.id;}
  const signature=JSON.stringify(payload);if(!pendingEntry||pendingEntry.signature!==signature)pendingEntry={signature,id:uid()};
  const editing=ui.editing;
  await perform(()=>api(editing?'/api/time-entries/'+encodeURIComponent(editing):'/api/time-entries',editing?'PATCH':'POST',{...payload,request_id:pendingEntry.id}),()=>{ui.day=date;ui.month=monthStart(date);ui.filter='all';clearEditing();ui.composerOpen=false;},editing?'Запись обновлена.':'Добавлено '+format(seconds)+' · '+payload.project+'.');
}
async function startTimer(id){
  closeDayDetails();
  await perform(()=>api('/api/timer/start','POST',{task_id:id}),()=>{ui.mode='timer';ui.view='journal';ui.editing=null;ui.composerOpen=true;},'Таймер запущен.');
}
async function startFromComposer(){
  const title=q('[data-task]').value.trim(),project=q('[data-project]').value;if(!title)return showError('Укажите задачу для таймера.');
  await perform(async()=>{let t=data.tasks.find(t=>!t.archived&&t.project===project&&normalize(t.title)===normalize(title));if(!t)t=await api('/api/tasks','POST',{project,title});return api('/api/timer/start','POST',{task_id:t.id});},()=>{ui.mode='timer';ui.view='journal';ui.editing=null;ui.composerOpen=true;},'Таймер запущен.');
}
q('[data-entry-form]').addEventListener('submit',event=>{event.preventDefault();if(ui.mode==='manual')saveEntry();else if(!data.timer)startFromComposer();});
q('[data-project]').addEventListener('change',updateOptions);
q('[data-start]').addEventListener('click',startFromComposer);
q('[data-return-timer]').addEventListener('click',()=>{ui.editing=null;openComposer('timer');});
q('[data-toggle-composer]').addEventListener('click',()=>{if(busy||!ready)return;ui.composerOpen=!ui.composerOpen;renderComposer();actionStates();if(ui.composerOpen)q('[data-task]').focus();});
q('[data-close-composer]').addEventListener('click',()=>{if(busy)return;ui.composerOpen=false;renderComposer();});
q('[data-add-day]').addEventListener('click',()=>{if(busy)return;clearEditing();q('[data-date]').value=ui.day;openComposer('manual');});
q('[data-detail-all]').addEventListener('click',()=>{ui.filter='all';renderCalendar();renderEntries();actionStates();});
q('[data-pause]').addEventListener('click',()=>{const timer=data.timer;if(!timer)return;const paused=timer.status==='paused';perform(()=>api('/api/timer/'+encodeURIComponent(timer.id)+(paused?'/resume':'/pause'),'POST',{}),null,paused?'Таймер продолжен.':'Таймер на паузе.');});
q('[data-stop]').addEventListener('click',()=>{const timer=data.timer;if(!timer)return;perform(()=>api('/api/timer/'+encodeURIComponent(timer.id)+'/stop','POST',{}),result=>{const dates=(result.entries||[]).map(e=>e.work_date).filter(isDate).sort();ui.mode='manual';ui.day=dates.at(-1)||data.today;ui.month=monthStart(ui.day);ui.filter='all';q('[data-date]').value=ui.day;clearEditing();ui.composerOpen=false;},'Таймер завершен. Записи добавлены в календарь.');});
q('[data-cancel]').addEventListener('click',()=>{clearEditing();showError('');notice('Редактирование отменено.');});
q('[data-delete]').addEventListener('click',()=>{const id=ui.editing;if(!id)return;perform(()=>api('/api/time-entries/'+encodeURIComponent(id),'DELETE',{}),()=>{removedId=id;clearEditing();q('[data-undo]').hidden=false;},'Запись удалена.');});
q('[data-undo]').addEventListener('click',()=>{if(!removedId)return;perform(()=>api('/api/time-entries/'+encodeURIComponent(removedId)+'/restore','POST',{}),()=>{removedId=null;q('[data-undo]').hidden=true;},'Запись восстановлена.');});
q('[data-today]').addEventListener('click',async()=>{if(busy||!ready)return;ui.month=monthStart(data.today);selectedDay(data.today);await refresh();});
q('[data-show-archive]').addEventListener('change',event=>{ui.showArchive=event.target.checked;renderTasks();actionStates();});
q('[data-task-form]').addEventListener('submit',event=>{event.preventDefault();const title=q('[data-new-task]').value.trim();if(!title)return showError('Укажите название задачи.');perform(()=>api('/api/tasks','POST',{project:q('[data-new-project]').value,title}),()=>{q('[data-new-task]').value='';},'Задача сохранена.');});
q('[data-create-backup]').addEventListener('click',()=>perform(()=>api('/api/backups','POST',{}),()=>{loadBackups();},'Резервная копия создана.'));
q('[data-rename-form]').addEventListener('submit',event=>{event.preventDefault();const title=q('[data-rename-title]').value.trim();if(!title)return;const id=renameId;q('[data-rename-dialog]').close();perform(()=>api('/api/tasks/'+encodeURIComponent(id),'PATCH',{title}),null,'Название задачи обновлено.');});
q('[data-restore-form]').addEventListener('submit',event=>{event.preventDefault();const name=restoreName;q('[data-restore-dialog]').close();perform(()=>api('/api/backups/restore','POST',{name}),()=>{clearEditing();removedId=null;q('[data-undo]').hidden=true;loadBackups();},'Данные восстановлены. Предыдущая версия сохранена в резервной копии.');});
qa('[data-close-dialog]').forEach(b=>b.addEventListener('click',()=>b.closest('dialog').close()));
root.addEventListener('click',async event=>{
  const b=event.target.closest('button');if(!b||b.disabled||busy)return;
  if(b.dataset.view){ui.view=b.dataset.view;render();if(ui.view==='data')loadBackups();}
  if(b.dataset.mode){if(ui.editing)clearEditing();ui.mode=b.dataset.mode;showError('');renderComposer();actionStates();}
  if(b.dataset.month&&ready){if(ui.editing)clearEditing();ui.month=shiftMonth(ui.month,Number(b.dataset.month));await refresh();}
  if(b.dataset.day&&ready){selectedDay(b.dataset.day);q('[data-day-dialog]').showModal();}
  if(b.dataset.filter){ui.filter=b.dataset.filter;renderCalendar();renderEntries();actionStates();}
  if(b.dataset.run)startTimer(b.dataset.run);
  if(b.dataset.edit){
    const entry=data.entries.find(e=>e.id===b.dataset.edit),t=task(entry?.task_id);if(!entry||!t)return;
    ui.editing=entry.id;ui.mode='manual';q('[data-project]').value=t.project;q('[data-task]').value=t.title;q('[data-date]').value=entry.work_date;q('[data-duration]').value=clock(entry.duration_seconds);q('[data-note]').value=entry.note||'';
    showError('');openComposer('manual');notice('Редактирование записи · '+t.project+'.');
  }
  if(b.dataset.rename){const t=task(b.dataset.rename);if(!t)return;renameId=t.id;q('[data-rename-title]').value=t.title;q('[data-rename-dialog]').showModal();}
  if(b.dataset.archive){const t=task(b.dataset.archive);if(!t)return;perform(()=>api('/api/tasks/'+encodeURIComponent(t.id),'PATCH',{archived:!t.archived}),null,t.archived?'Задача возвращена из архива.':'Задача архивирована. История времени сохранена.');}
  if(b.dataset.backupRestore){restoreName=b.dataset.backupRestore;q('[data-restore-name]').textContent=restoreName;q('[data-restore-dialog]').showModal();}
});
document.addEventListener('visibilitychange',()=>{if(!document.hidden&&!busy)refresh();});
window.addEventListener('online',()=>{if(!busy)refresh();});
setInterval(tick,500);
setInterval(()=>{if(!busy&&!document.hidden)refresh();},8000);
actionStates();refresh({initial:true});
