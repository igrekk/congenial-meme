export const PROJECTS = ['INAL','INAC','GCVU','ECGP'];

const parseDate = value => new Date(value+'T12:00:00Z');
export const addDays = (value,count) => {
  const date=parseDate(value);date.setUTCDate(date.getUTCDate()+count);
  return date.toISOString().slice(0,10);
};
export const monthStart = value => value.slice(0,7)+'-01';
export const shiftMonth = (value,delta) => {
  const date=parseDate(monthStart(value));date.setUTCMonth(date.getUTCMonth()+delta);
  return date.toISOString().slice(0,10);
};
export function calendarRange(value){
  const start=monthStart(value),end=addDays(shiftMonth(start,1),-1);
  const from=addDays(start,-((parseDate(start).getUTCDay()+6)%7));
  const to=addDays(end,6-((parseDate(end).getUTCDay()+6)%7));
  const days=[];for(let date=from;date<=to;date=addDays(date,1))days.push(date);
  return {from,to,days,monthStart:start,monthEnd:end};
}
export const monthEntries = (entries,month) => {
  const prefix=monthStart(month).slice(0,7)+'-';
  return entries.filter(entry=>entry.work_date.startsWith(prefix));
};
export function projectTotals(entries,tasks){
  const projectByTask=new Map(tasks.map(task=>[task.id,task.project]));
  const totals=Object.fromEntries(PROJECTS.map(project=>[project,0]));
  for(const entry of entries){const project=projectByTask.get(entry.task_id);if(Object.hasOwn(totals,project))totals[project]+=entry.duration_seconds;}
  return totals;
}
export function formatHours(seconds){
  if(seconds>0&&seconds<60)return '<1 мин';
  return new Intl.NumberFormat('ru-RU',{maximumFractionDigits:2}).format(seconds/3600)+' ч';
}
