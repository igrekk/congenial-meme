const {test,before}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
let intervals;
before(async()=>{
  const source=fs.readFileSync(path.join(__dirname,'../static/intervals.js'),'utf8');
  intervals=await import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'));
});
const bounds=changes=>intervals.calculateInterval({mode:'bounds',startDate:'2026-11-20',startTime:'09:00',endDate:'2026-11-20',endTime:'11:30',...changes});
const duration=changes=>intervals.calculateInterval({mode:'duration',startDate:'2026-11-30',startTime:'23:30',duration:'2,5',...changes});

test('Start and finish produce two and a half hours with persisted endpoints',()=>{
  const result=bounds();
  assert.equal(result.valid,true);
  assert.equal(result.duration_seconds,9000);
  assert.equal(result.started_at,'2026-11-20T09:00:00');
  assert.equal(result.ended_at,'2026-11-20T11:30:00');
});

test('Start plus duration advances the ending date across the month',()=>{
  const result=duration();
  assert.equal(result.valid,true);
  assert.equal(result.ended_at,'2026-12-01T02:00:00');
  assert.equal(result.duration_seconds,9000);
});

test('December and leap-year boundaries preserve real calendar dates',()=>{
  assert.equal(duration({startDate:'2026-12-31'}).ended_at,'2027-01-01T02:00:00');
  assert.equal(duration({startDate:'2024-02-28'}).ended_at,'2024-02-29T02:00:00');
  assert.equal(duration({startDate:'2026-02-28'}).ended_at,'2026-03-01T02:00:00');
});

test('Ending before or at the start is rejected instead of guessing the next day',()=>{
  assert.equal(bounds({endTime:'08:00'}).valid,false);
  assert.equal(bounds({endTime:'09:00'}).valid,false);
  const overnight=bounds({startTime:'23:00',endDate:'2026-11-21',endTime:'01:00'});
  assert.equal(overnight.duration_seconds,7200);
});

test('Seconds remain exact in both entry methods',()=>{
  assert.equal(bounds({startTime:'10:00:15',endTime:'10:01:00'}).duration_seconds,45);
  assert.equal(duration({startTime:'23:59:45',duration:'00:00:30'}).ended_at,'2026-12-01T00:00:15');
});

test('Legacy records retain duration without an invented start time',()=>{
  const result=intervals.calculateInterval({mode:'duration_only',startDate:'2026-11-20',duration:'2,5'});
  assert.equal(result.valid,true);
  assert.equal(result.duration_seconds,9000);
  assert.equal(result.started_at,null);
  assert.equal(result.ended_at,null);
});

test('Invalid dates, times and incomplete fields are handled explicitly',()=>{
  assert.equal(bounds({startDate:'2026-02-30'}).valid,false);
  assert.equal(bounds({startTime:'24:00'}).valid,false);
  assert.equal(bounds({endTime:'11:60'}).valid,false);
  assert.equal(bounds({startTime:''}).incomplete,true);
  assert.equal(intervals.wallDateTime('2026-11-20','09:00'),'2026-11-20T09:00:00');
});

test('Long intervals are bounded while legacy entries remain at most one day',()=>{
  assert.equal(intervals.parseDuration('744:00'),31*86400);
  assert.equal(Number.isNaN(intervals.parseDuration('745:00')),true);
  assert.equal(intervals.calculateInterval({mode:'legacy',startDate:'2026-11-20',duration:'24:00:01'}).valid,false);
  assert.equal(duration({startDate:'9999-12-31'}).valid,false);
});

test('Moscow field arithmetic is independent of the computer time zone',()=>{
  const previous=process.env.TZ;
  try{
    for(const zone of ['UTC','America/Los_Angeles','Asia/Tokyo']){
      process.env.TZ=zone;
      assert.equal(duration().ended_at,'2026-12-01T02:00:00');
      assert.equal(bounds().duration_seconds,9000);
    }
  }finally{if(previous===undefined)delete process.env.TZ;else process.env.TZ=previous;}
});
