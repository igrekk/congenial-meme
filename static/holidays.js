export const HOLIDAY_COUNTRIES = Object.freeze([
  {code:'BY',name:'Беларусь'},
  {code:'GE',name:'Грузия'},
  {code:'KZ',name:'Казахстан'},
  {code:'CZ',name:'Чехия'}
]);

const COUNTRY_CODES = new Set(HOLIDAY_COUNTRIES.map(country=>country.code));
const OFFICIAL_HOSTS = ['pravo.by','etalonline.by','government.by','president.gov.by','gov.by','matsne.gov.ge','gov.ge','gov.kz','egov.kz','adilet.zan.kz','gov.cz','mpsv.cz'];
const STORAGE_KEY = 'work-time.holiday-countries.v1';
const instances = new WeakMap();
const kindLabels = {holiday:'Праздник',weekend:'Выходной',transfer:'Перенесенный выходной',workday:'Рабочий день'};

export function safeHolidaySource(value){
  try{
    const url=new URL(value);
    return url.protocol==='https:'&&(OFFICIAL_HOSTS.some(host=>url.hostname===host||url.hostname.endsWith('.'+host))||(url.hostname==='t.me'&&url.pathname.startsWith('/s/MintrudSocBelarus/')))?url.href:null;
  }catch{return null;}
}

export function holidayForDate(days,date){
  const matches=(Array.isArray(days)?days:[]).filter(day=>day&&day.date===date&&Object.hasOwn(kindLabels,day.kind));
  if(!matches.length)return null;
  const priority={holiday:4,transfer:3,workday:2,weekend:1};
  const sorted=[...matches].sort((a,b)=>priority[b.kind]-priority[a.kind]);
  const chosen=sorted[0];
  const names=[...new Set(matches.filter(day=>day.kind===chosen.kind).map(day=>String(day.name||kindLabels[day.kind])))];
  return {...chosen,name:names.join('; ')};
}

function element(tag,className,text){
  const node=document.createElement(tag);
  if(className)node.className=className;
  if(text!==undefined)node.textContent=text;
  return node;
}

function yearOf(value){
  const match=/^(\d{4})-\d{2}(?:-\d{2})?$/.exec(value||'');
  return match?Number(match[1]):null;
}

function preference(){
  try{
    const saved=JSON.parse(localStorage.getItem(STORAGE_KEY)||'null');
    if(saved&&Array.isArray(saved.configured)&&Array.isArray(saved.enabled)){
      const configured=[...new Set(['BY',...saved.configured.filter(code=>COUNTRY_CODES.has(code))])];
      return {configured:new Set(configured),enabled:new Set(saved.enabled.filter(code=>configured.includes(code)))};
    }
  }catch{}
  return {configured:new Set(['BY']),enabled:new Set(['BY'])};
}

function dateLabel(value){
  if(!value)return '';
  const date=new Date(typeof value==='number'?value*1000:value);
  return Number.isFinite(date.getTime())?date.toLocaleDateString('ru-RU'):'';
}

/** Adds a country layer without owning or replacing the work-entry calendar. */
export function createHolidayLayer(root){
  if(instances.has(root))return instances.get(root);
  const choices=preference();
  const cache=new Map(),pending=new Map(),lastAttempt=new Map(),failures=new Map();
  let month=null,detailDate=null,loadingAction=false;
  let controls=root.querySelector('[data-holiday-controls]');
  if(!controls){
    controls=element('div');controls.setAttribute('data-holiday-controls','');
    const filters=root.querySelector('[data-filters]');
    if(filters)filters.after(controls);else root.querySelector('.tt-calendar')?.prepend(controls);
  }
  controls.classList.add('tt-holiday-controls');
  let detail=root.querySelector('[data-day-holidays]');
  if(!detail){
    detail=element('div');detail.setAttribute('data-day-holidays','');
    const entries=root.querySelector('[data-day-dialog] [data-entries]');
    if(entries)entries.before(detail);
  }
  detail.classList.add('tt-day-holidays');
  const toolbar=element('div','tt-holiday-toolbar');
  const countryList=element('div','tt-holiday-country-list');
  countryList.setAttribute('aria-label','Календари стран');
  const addButton=element('button','tt-button tt-holiday-add','＋ Календари стран');
  addButton.type='button';addButton.setAttribute('data-holiday-add','');
  const refreshButton=element('button','tt-button tt-quiet tt-holiday-refresh','Обновить');
  refreshButton.type='button';refreshButton.setAttribute('data-holiday-refresh','');
  toolbar.append(countryList,addButton,refreshButton);
  const status=element('p','tt-holiday-status');
  status.setAttribute('aria-live','polite');status.setAttribute('data-holiday-status','');
  const sources=element('details','tt-holiday-sources');
  sources.setAttribute('data-holiday-sources','');
  const sourceSummary=element('summary',null,'Официальные источники');
  const sourceContent=element('div','tt-holiday-source-content');
  sources.append(sourceSummary,sourceContent);
  controls.replaceChildren(toolbar,status,sources);

  const dialog=element('dialog','tt-holiday-dialog');
  dialog.setAttribute('aria-labelledby','tt-holiday-dialog-title');
  dialog.setAttribute('data-holiday-dialog','');
  const dialogHeading=element('div','tt-holiday-dialog-heading');
  const title=element('h2',null,'Подключить календари стран');title.id='tt-holiday-dialog-title';
  const closeButton=element('button','tt-icon-button','✕');
  closeButton.type='button';closeButton.setAttribute('aria-label','Закрыть выбор стран');
  const dialogYear=element('p','tt-small tt-muted');
  const dialogChoices=element('div','tt-holiday-options');
  const dialogError=element('p','tt-holiday-error');dialogError.setAttribute('role','alert');dialogError.hidden=true;
  const actions=element('div','tt-dialog-actions');
  const cancelButton=element('button','tt-button','Отмена');cancelButton.type='button';
  const loadButton=element('button','tt-button tt-primary','Загрузить');loadButton.type='button';loadButton.setAttribute('data-holiday-load','');
  actions.append(cancelButton,loadButton);dialogHeading.append(title,closeButton);
  dialog.append(dialogHeading,dialogYear,dialogChoices,dialogError,actions);root.append(dialog);

  function persist(){
    try{localStorage.setItem(STORAGE_KEY,JSON.stringify({configured:[...choices.configured],enabled:[...choices.enabled]}));}catch{}
  }
  function enabledCountries(){return HOLIDAY_COUNTRIES.filter(country=>choices.enabled.has(country.code));}
  function currentYears(){
    const years=new Set();
    if(yearOf(month))years.add(yearOf(month));
    root.querySelectorAll('[data-day]').forEach(button=>{const year=yearOf(button.dataset.day);if(year)years.add(year);});
    return [...years];
  }
  function sourceLink(source){
    const href=safeHolidaySource(source?.url);if(!href)return null;
    const link=element('a',null,source.title||new URL(href).hostname);
    link.href=href;link.target='_blank';link.rel='noopener noreferrer';return link;
  }
  function renderCountryChoices(){
    countryList.replaceChildren();
    for(const country of HOLIDAY_COUNTRIES.filter(item=>choices.configured.has(item.code))){
      const label=element('label','tt-holiday-country tt-holiday-country-'+country.code);
      const input=element('input');input.type='checkbox';input.checked=choices.enabled.has(country.code);
      input.setAttribute('data-holiday-country',country.code);
      const marker=element('span','tt-holiday-swatch');marker.setAttribute('aria-hidden','true');
      label.append(input,marker,element('span',null,country.name));countryList.append(label);
      input.addEventListener('change',()=>{
        if(input.checked)choices.enabled.add(country.code);else choices.enabled.delete(country.code);
        persist();apply();loadVisible();
      });
    }
  }
  function renderSources(){
    sourceContent.replaceChildren();
    const year=yearOf(month);if(!year)return;
    for(const country of enabledCountries()){
      const data=cache.get(country.code+':'+year);
      const block=element('section','tt-holiday-source');
      block.append(element('h3',null,country.name+' · '+year));
      if(!data){block.append(element('p','tt-small tt-muted',failures.get(country.code+':'+year)||'Календарь загружается…'));sourceContent.append(block);continue;}
      const coverage={verified:'Подтвержденный календарь',baseline:'Базовые праздники',cached:'Сохраненный календарь'}[data.coverage]||'Календарь';
      const checked=dateLabel(data.checked_at||data.loaded_at);
      block.append(element('p','tt-small tt-muted',coverage+(checked?' · проверка '+checked:'')));
      if(data.status_message)block.append(element('p','tt-small',data.status_message));
      const list=element('ul');
      for(const source of data.sources||[]){const link=sourceLink(source);if(link){const row=element('li');row.append(link);list.append(row);}}
      if(list.childNodes.length)block.append(list);
      sourceContent.append(block);
    }
    if(!choices.enabled.size)sourceContent.append(element('p','tt-small tt-muted','Включите календарь страны, чтобы показать источники.'));
  }
  function renderStatus(){
    const year=yearOf(month);const countries=enabledCountries();
    refreshButton.disabled=loadingAction||countries.length===0;
    addButton.disabled=loadingAction;
    const loading=countries.some(country=>currentYears().some(item=>pending.has(country.code+':'+item)));
    const failed=countries.filter(country=>failures.has(country.code+':'+year));
    const baseline=countries.filter(country=>cache.get(country.code+':'+year)?.coverage==='baseline');
    status.classList.toggle('tt-holiday-error',failed.length>0);
    status.textContent=!countries.length?'Календари стран скрыты.':loading?'Загружаем календари…':failed.length?'Не удалось загрузить: '+failed.map(country=>country.name).join(', ')+'. Повторите загрузку.':baseline.length?'Пятидневная неделя · '+baseline.map(country=>country.name).join(', ')+': переносы требуют подтверждения на '+year+' год.':'Пятидневная неделя · выходные и праздники';
  }
  function updateDetails(date){
    detailDate=date;detail.replaceChildren();
    if(!date)return;
    for(const country of enabledCountries()){
      const data=cache.get(country.code+':'+yearOf(date));
      const event=holidayForDate(data?.days,date);if(!event)continue;
      const row=element('div','tt-day-holiday tt-holiday-country-'+country.code);
      const label=element('span','tt-day-holiday-country');
      const marker=element('span','tt-holiday-swatch');marker.setAttribute('aria-hidden','true');label.append(marker,element('span',null,country.name));
      const text=element('span','tt-day-holiday-name',event.name||kindLabels[event.kind]);
      row.append(label,text);
      const href=safeHolidaySource(event.source_url)||safeHolidaySource(data?.sources?.[0]?.url);
      if(href){const link=element('a','tt-small','Источник');link.href=href;link.target='_blank';link.rel='noopener noreferrer';row.append(link);}
      detail.append(row);
    }
    detail.hidden=!detail.childNodes.length;
  }
  function apply(){
    for(const button of root.querySelectorAll('[data-day]')){
      button.querySelectorAll('[data-holiday-badges]').forEach(node=>node.remove());
      button.classList.remove('tt-country-rest','tt-country-holiday','tt-country-workday');
      const date=button.dataset.day;
      if(!button.dataset.holidayBaseLabel)button.dataset.holidayBaseLabel=button.getAttribute('aria-label')||date;
      const descriptions=[];const markers=element('span','tt-calendar-country-markers');markers.setAttribute('data-holiday-badges','');markers.setAttribute('aria-hidden','true');
      for(const country of enabledCountries()){
        const data=cache.get(country.code+':'+yearOf(date));
        const event=holidayForDate(data?.days,date);if(!event)continue;
        const badge=element('span','tt-holiday-badge tt-holiday-country-'+country.code+' tt-holiday-badge--'+event.kind,country.code);
        if(event.kind==='workday')badge.append(element('span','tt-holiday-work-symbol','↑'));
        badge.title=country.name+': '+(event.name||kindLabels[event.kind]);
        markers.append(badge);descriptions.push(country.name+': '+(event.name||kindLabels[event.kind]));
        if(country.code==='BY'){
          if(event.kind==='weekend'||event.kind==='holiday'||event.kind==='transfer')button.classList.add('tt-country-rest');
          if(event.kind==='holiday'||event.kind==='transfer')button.classList.add('tt-country-holiday');
          if(event.kind==='workday')button.classList.add('tt-country-workday');
        }
      }
      if(markers.childNodes.length)button.append(markers);
      button.setAttribute('aria-label',button.dataset.holidayBaseLabel+(descriptions.length?'; '+descriptions.join('; '):''));
    }
    updateDetails(detailDate);renderStatus();renderSources();
  }
  async function request(country,year,refresh=false){
    const key=country+':'+year;
    if(pending.has(key))return pending.get(key);
    if(!refresh&&cache.has(key))return cache.get(key);
    if(!refresh&&Date.now()-(lastAttempt.get(key)||0)<45000)return null;
    lastAttempt.set(key,Date.now());failures.delete(key);
    const operation=(async()=>{
      const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),20000);
      try{
        const response=await fetch(refresh?'/api/holidays/load':'/api/holidays?country='+country+'&year='+year,{method:refresh?'POST':'GET',cache:'no-store',signal:controller.signal,headers:refresh?{'Content-Type':'application/json','X-Time-Tracker':'1'}:{},...(refresh?{body:JSON.stringify({country,year,refresh:true})}:{})});
        let data;try{data=await response.json();}catch{throw new Error('Не удалось прочитать календарь.');}
        if(!response.ok)throw new Error(data.error||'Календарь временно недоступен.');
        if(!data||data.country!==country||Number(data.year)!==year||!Array.isArray(data.days))throw new Error('Сервер вернул календарь другой страны или года.');
        cache.set(key,data);failures.delete(key);return data;
      }catch(error){
        failures.set(key,error.name==='AbortError'?'Источник не ответил вовремя.':error.message||'Не удалось загрузить календарь.');return null;
      }finally{clearTimeout(timeout);pending.delete(key);apply();}
    })();
    pending.set(key,operation);renderStatus();return operation;
  }
  async function loadVisible(refresh=false){
    const jobs=[];
    for(const country of enabledCountries())for(const year of currentYears())jobs.push(request(country.code,year,refresh));
    await Promise.all(jobs);apply();
  }
  function openPicker(){
    dialogChoices.replaceChildren();dialogError.hidden=true;
    const year=yearOf(month);dialogYear.textContent='Праздники и выходные за '+(year||'выбранный')+' год. Каждая страна выделяется своим цветом.';
    for(const country of HOLIDAY_COUNTRIES.filter(item=>item.code!=='BY')){
      const label=element('label','tt-holiday-option tt-holiday-country-'+country.code);
      const input=element('input');input.type='checkbox';input.value=country.code;input.checked=choices.configured.has(country.code)&&choices.enabled.has(country.code);input.setAttribute('data-holiday-option',country.code);
      const marker=element('span','tt-holiday-swatch');marker.setAttribute('aria-hidden','true');label.append(input,marker,element('span',null,country.name));dialogChoices.append(label);
    }
    dialog.showModal();
  }
  addButton.addEventListener('click',openPicker);
  closeButton.addEventListener('click',()=>dialog.close());cancelButton.addEventListener('click',()=>dialog.close());
  loadButton.addEventListener('click',async()=>{
    const selected=[...dialogChoices.querySelectorAll('input:checked')].map(input=>input.value);
    if(!selected.length){dialogError.textContent='Выберите хотя бы одну страну.';dialogError.hidden=false;return;}
    const year=yearOf(month);if(!year)return;
    loadingAction=true;loadButton.disabled=true;dialogError.hidden=true;loadButton.textContent='Загружаем…';renderStatus();
    for(const code of selected){choices.configured.add(code);choices.enabled.add(code);}
    persist();renderCountryChoices();
    await Promise.all(selected.map(code=>request(code,year,true)));
    loadingAction=false;loadButton.disabled=false;loadButton.textContent='Загрузить';dialog.close();apply();loadVisible();
  });
  refreshButton.addEventListener('click',async()=>{
    if(loadingAction)return;loadingAction=true;renderStatus();await loadVisible(true);loadingAction=false;apply();
  });
  function render(value){
    month=value;renderCountryChoices();apply();if(yearOf(month))loadVisible();
  }
  const api={render,updateDetails,refresh:()=>loadVisible(true)};
  instances.set(root,api);renderCountryChoices();return api;
}
