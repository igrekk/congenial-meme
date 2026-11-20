const {test,before}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
let calendar;
before(async()=>{
  const source=fs.readFileSync(path.join(__dirname,'../static/calendar.js'),'utf8');
  calendar=await import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'));
});

test('November 2026 aligns Monday to Sunday across six rows',()=>{
  const range=calendar.calendarRange('2026-11-20');
  assert.equal(range.from,'2026-10-26');
  assert.equal(range.to,'2026-12-06');
  assert.equal(range.days.length,42);
  assert.equal(new Set(range.days).size,42);
  assert.equal(range.days.filter(day=>day.startsWith('2026-11-')).length,30);
  assert.equal(range.monthStart,'2026-11-01');
  assert.equal(range.monthEnd,'2026-11-30');
});

test('February starting on Monday uses four complete rows',()=>{
  const range=calendar.calendarRange('2021-02-15');
  assert.equal(range.from,'2021-02-01');
  assert.equal(range.to,'2021-02-28');
  assert.equal(range.days.length,28);
});

test('Leap day appears exactly once in February 2024',()=>{
  const range=calendar.calendarRange('2024-02-15');
  assert.equal(range.days.filter(day=>day==='2024-02-29').length,1);
  assert.equal(range.monthEnd,'2024-02-29');
  assert.equal(range.days.length%7,0);
  assert.equal(calendar.calendarRange('2026-02-15').monthEnd,'2026-02-28');
});

test('Month navigation crosses years without spilling from long months',()=>{
  assert.equal(calendar.shiftMonth('2026-01-31',-1),'2025-12-01');
  assert.equal(calendar.shiftMonth('2026-12-31',1),'2027-01-01');
  assert.equal(calendar.shiftMonth('2024-03-31',-1),'2024-02-01');
  assert.equal(calendar.shiftMonth('2026-11-20',0),'2026-11-01');
});

test('Month totals include boundary dates and exclude neighboring cells',()=>{
  const entries=[
    {work_date:'2026-10-31',duration_seconds:28800},
    {work_date:'2026-11-01',duration_seconds:5400},
    {work_date:'2026-11-30',duration_seconds:18000},
    {work_date:'2026-12-01',duration_seconds:21600}
  ];
  const november=calendar.monthEntries(entries,'2026-11-20');
  assert.equal(november.length,2);
  assert.equal(november.reduce((seconds,e)=>seconds+e.duration_seconds,0),23400);
});

test('Several tasks and sessions of one project produce one project total',()=>{
  const tasks=[{id:'a',project:'INAL'},{id:'b',project:'INAL'},{id:'c',project:'INAC'}];
  const totals=calendar.projectTotals([
    {task_id:'a',duration_seconds:9000},
    {task_id:'b',duration_seconds:3600},
    {task_id:'a',duration_seconds:1800},
    {task_id:'c',duration_seconds:5400}
  ],tasks);
  assert.deepEqual(totals,{INAL:14400,INAC:5400,GCVU:0,ECGP:0});
});

test('Archived tasks retain hours and empty days have four zero totals',()=>{
  assert.equal(calendar.projectTotals([{task_id:'archived',duration_seconds:4500}],
    [{id:'archived',project:'ECGP',archived:true}]).ECGP,4500);
  assert.deepEqual(calendar.projectTotals([],[]),{INAL:0,INAC:0,GCVU:0,ECGP:0});
});

test('Compact hours use decimal comma and keep short sessions visible',()=>{
  assert.equal(calendar.formatHours(9000),'2,5 ч');
  assert.equal(calendar.formatHours(12600),'3,5 ч');
  assert.equal(calendar.formatHours(30),'<1 мин');
  assert.equal(calendar.formatHours(3600),'1 ч');
});
