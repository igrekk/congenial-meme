// Civil date/time arithmetic for the application's Moscow wall-clock fields.
// UTC setters deliberately avoid using the browser's own time zone.
export const MAX_INTERVAL_SECONDS=31*24*60*60;

export function parseWallDateTime(value){
  if(typeof value!=='string')return NaN;
  const match=/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2}))?$/.exec(value);
  if(!match)return NaN;
  const [year,month,day,hour,minute,second]=match.slice(1).map(part=>Number(part||0));
  if(year<1||year>9999||month<1||month>12||day<1||day>31||hour>23||minute>59||second>59)return NaN;
  const date=new Date(0);date.setUTCFullYear(year,month-1,day);date.setUTCHours(hour,minute,second,0);
  if(date.getUTCFullYear()!==year||date.getUTCMonth()!==month-1||date.getUTCDate()!==day)return NaN;
  return date.getTime();
}

export function wallDateTime(date,time){
  const value=date+'T'+time,stamp=parseWallDateTime(value);
  return Number.isFinite(stamp)?new Date(stamp).toISOString().slice(0,19):null;
}

export function splitWallDateTime(value){
  const stamp=parseWallDateTime(value);
  if(!Number.isFinite(stamp))return {date:'',time:''};
  const normalized=new Date(stamp).toISOString();
  return {date:normalized.slice(0,10),time:normalized.slice(11,19)};
}

export function parseDuration(value,maximum=MAX_INTERVAL_SECONDS){
  if(typeof value!=='string')return NaN;
  const text=value.trim().replace(',','.');let seconds;
  if(/^\d{1,3}:\d{2}(:\d{2})?$/.test(text)){
    const parts=text.split(':').map(Number);
    if(parts[1]>59||(parts[2]||0)>59)return NaN;
    seconds=parts[0]*3600+parts[1]*60+(parts[2]||0);
  }else if(/^\d+(\.\d{1,5})?$/.test(text))seconds=Math.round(Number(text)*3600);
  else return NaN;
  return Number.isInteger(seconds)&&seconds>0&&seconds<=maximum?seconds:NaN;
}

export function normalizeMinuteDuration(value,maximum=MAX_INTERVAL_SECONDS){
  let seconds=parseDuration(value,maximum);
  if(!Number.isFinite(seconds)&&typeof value==='string'){
    const text=value.trim().replace(',','.');
    if(/^\d+(\.\d{1,5})?$/.test(text)){
      const exact=Number(text)*3600;
      if(exact>0&&exact<=maximum)seconds=exact;
    }
  }
  if(!Number.isFinite(seconds))return NaN;
  return Math.min(maximum,Math.max(60,Math.round(seconds/60)*60));
}

export function calculateInterval({mode='bounds',startDate='',startTime='',endDate='',endTime='',duration=''}){
  const fail=(error,incomplete=false)=>({valid:false,incomplete,error});
  if(mode==='duration_only'||mode==='legacy'){
    if(!startDate||!duration)return fail('Укажите дату и длительность.',true);
    if(!wallDateTime(startDate,'00:00'))return fail('Укажите существующую дату.');
    const seconds=parseDuration(duration,86400);
    if(!Number.isFinite(seconds))return fail('Введите длительность от 1 секунды до 24 часов: например 2,5 или 02:30.');
    return {valid:true,incomplete:false,work_date:startDate,duration_seconds:seconds,started_at:null,ended_at:null};
  }
  if(!startDate||!startTime)return fail('Укажите дату и время начала.',true);
  const started_at=wallDateTime(startDate,startTime);
  if(!started_at)return fail('Укажите корректные дату и время начала.');
  const start=parseWallDateTime(started_at);let end,seconds;
  if(mode==='bounds'){
    if(!endDate||!endTime)return fail('Укажите дату и время окончания.',true);
    const value=wallDateTime(endDate,endTime);
    if(!value)return fail('Укажите корректные дату и время окончания.');
    end=parseWallDateTime(value);seconds=(end-start)/1000;
    if(seconds<=0)return fail('Окончание должно быть позже начала. Для работы через полночь выберите следующую дату.');
    if(seconds>MAX_INTERVAL_SECONDS)return fail('Один интервал может длиться не более 31 дня.');
  }else if(mode==='duration'){
    if(!duration)return fail('Укажите длительность работы.',true);
    seconds=parseDuration(duration);
    if(!Number.isFinite(seconds))return fail('Введите длительность от 1 секунды до 31 дня: например 2,5 или 02:30.');
    end=start+seconds*1000;
    if(new Date(end).getUTCFullYear()>9999)return fail('Дата окончания выходит за допустимый диапазон.');
  }else return fail('Выберите способ расчета времени.');
  return {valid:true,incomplete:false,work_date:startDate,started_at,ended_at:new Date(end).toISOString().slice(0,19),duration_seconds:seconds};
}
