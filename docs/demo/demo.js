/* Public demo transport. Entirely local, deterministic fixtures; no API calls. */
(function () {
  "use strict";
  const config = window.JevDemoConfig, params = new URLSearchParams(location.search);
  const scenario = ["mini", "parallel", "retry"].includes(params.get("scenario")) ? params.get("scenario") : "parallel";
  const isMini = scenario === "mini", retry = scenario === "retry";
  let speed = [1, 2, 4].includes(Number(params.get("speed"))) ? Number(params.get("speed")) : 1;
  let paused = params.get("paused") === "1", tick = 0, seq = 0, timer = null, stream = null;
  const history = [];
  const iso = () => new Date().toISOString();
  const copy = x => JSON.parse(JSON.stringify(x));
  const labels = config.labels;
  const task = (id, title, agent, files, depends = []) => ({
    task_id: id, title, agent: null, suggested_agent: agent, status: depends.length ? "pending" : "ready",
    type: agent === "sonnet" ? "test" : "code", complexity: agent === "luna" ? "S" : "M",
    module: "M1", depends_on: depends, files, description: title + ". Kabul ölçütlerini karşıla ve davranış kontrollerini çalıştır.",
    acceptance: ["İlgili davranış testleri geçmeli.", "Diğer görevlerin sözleşmeleri korunmalı."],
    covers: ["K1"], origin: "plan", round: 1, attempt: 0, attempts: [], decisions: [], preferred: agent,
  });
  const tasks = isMini ? [task("T01", "Tuş takımlı hesap makinesi", "luna", ["index.html", "hesap.js", "hesap.test.js"])] : retry ? [
    task("T01", "JSON kayıt katmanını yaz", "sol", ["store.py"]),
    task("T02", "Listeleme ve silme testleri", "sonnet", ["test_store.py"], ["T01"]),
    task("T03", "CLI komutlarını tamamla", "luna", ["cli.py"], ["T01"]),
  ] : [
    task("T01", "Sıcaklık dönüştürme modülü", "sol", ["sicaklik.py"]),
    task("T02", "Uzunluk dönüştürme modülü", "sonnet", ["uzunluk.py"]),
    task("T03", "CLI ve kullanım kılavuzu", "luna", ["cli.py", "README.md"], ["T01", "T02"]),
  ];
  const project = isMini ? "Hesap makinesi" : retry ? "Görev listesi CLI" : "Birim dönüştürücü CLI";
  const plan = {ok:true, project_name:project, summary:"Bağımlılıkları açık görevler, ayrı çalışma kopyaları ve doğrulanmış bir sonuç.",
    stack:{language:isMini ? "JavaScript" : "Python", frameworks:[]},
    modules:[{id:"M1", name:project, responsibility:"Uygulama ve davranış testleri", depends_on:[]}],
    success_criteria:[{id:"K1", statement:isMini ? "Dört işlem tuş takımından doğru sonuç verir." : retry ? "Görevler eklenir, listelenir, silinir ve dosyada saklanır." : "Dönüşümler ve komutlar doğru sonuç verir.", verification:"Birim ve uçtan uca testler"}],
    commands:{test:isMini ? "node --test hesap.test.js" : "python -m unittest discover"},
    assumptions:["Demo için örnek görevler ve olaylar kullanılır."], out_of_scope:["Bu sayfada gerçek ajan veya CLI çalıştırmak"], risks:[], html:""};
  const agents = copy(config.agents).map(a => ({...a, state:{state:"idle", text:"", seq:0}, activity:null,
    usage:{calls:0,input_tokens:0,output_tokens:0,cost_usd:0,duration_s:0}, quota:null}));
  const state = {ok:true, last_seq:0, server_now:iso(), labels, tasks, agents, plan:isMini ? null : plan,
    report:null, reports:[], feed:[], decisions:[], progress:{done:0,total:tasks.length,pct:0},
    run:{run_id:"demo", project_name:project, request:isMini ? "Çok basit bir hesap makinesi tasarla" : retry ? "Kayıtları kalıcı olan bir görev listesi CLI tasarla" : "Sıcaklık ve uzunluk birim dönüştürücü CLI tasarla",
      phase:isMini ? "sizing" : "planning", phase_since:iso(), round:1, dry_run:true, speed, work_s:0,
      branch:"jev/demo", reviewer:isMini ? "jev" : "sol", brain_limit:25, plan_approval:false,
      flow:isMini ? ["sizing","executing","reviewing","reported"] : ["planning","executing","reviewing","reported"],
      scale:{level:isMini ? "mini" : "orta",level_tr:isMini ? "Mini" : "Orta", difficulty:"orta",difficulty_tr:"Orta",source_tr:"Örnek senaryo",jev_review:isMini}},
    controls:{interactive:true,pending:false,duzelt:false,onayla:false,devam:false}};
  let report = null;
  const reportArchive = new Map();
  const emit = (type, data) => {
    const ev = {seq:++seq,ts:iso(),type,data:copy(data)};
    history.push(ev); state.last_seq = seq;
    if (type === "jev.decision") state.decisions.push(ev);
    if (["log","jev.dispatch","jev.decision","verify","run.phase","report.ready"].includes(type)) state.feed.push(ev);
    if (stream && stream.onmessage) stream.onmessage({data:JSON.stringify(ev)});
    return ev;
  };
  function agent(name, status, text, t = null, phase = "worker") {
    const a = agents.find(x => x.name === name);
    a.state = {agent:name,state:status,text,task_id:t,phase,seq:seq+1,ts:iso()};
    emit("agent.state", a.state);
    if (status === "editing" || status === "testing") {
      a.activity = {agent:name,kind:status,text,task_id:t,seq:seq+1,ts:iso()};
      emit("agent.activity", a.activity);
    }
  }
  function phase(p) {
    state.run.phase = p; state.run.phase_since = iso(); state.run.work_s = tick * 2;
    state.controls.duzelt = p === "reported";
    emit("run.phase", {phase:p,round:state.run.round,work_s:state.run.work_s});
  }
  function update(t, status) {
    t.status = status; t.status_tr = labels.status[status]; emit("task.update", t);
  }
  function dispatch(t) {
    t.agent = t.preferred; t.attempt++;
    agents.find(a => a.name === t.agent).usage.calls++;
    update(t,"running"); agent(t.agent,"editing",t.title,t.task_id);
    agent("jev","dispatch",t.task_id + " → " + labels.display[t.agent],t.task_id,"orchestrator");
    emit("jev.dispatch",{agent:t.agent,task_id:t.task_id,title:t.title});
  }
  function verify(t) {
    update(t,"verifying"); agent(t.agent,"testing","Davranış testlerini çalıştırıyor",t.task_id);
    agent("jev","verifying","Test sonuçlarını doğruluyor",t.task_id,"orchestrator");
  }
  function done(t) {
    t.summary = "Örnek davranış kontrolleri geçti.";
    t.attempts.push({n:t.attempt,agent:t.agent,outcome:"done",outcome_tr:"Başarılı",summary:t.summary,verify_pass:3,verify_total:3,duration_s:12});
    update(t,"done"); agent(t.agent,"done",t.title + " tamamlandı",t.task_id);
    emit("verify",{task_id:t.task_id,passed:true,command:plan.commands.test,summary:"3/3 örnek kontrol geçti"});
    emit("log",{level:"ok",text:t.task_id + " doğrulandı; koşu dalına kaydedildi."});
  }
  function finish() {
    const summary = "Örnek senaryo tamamlandı. Görevler doğrulandı; rapor hazır.";
    report = {ok:true,round:state.run.round,verdict:"basarili",verdict_tr:"Başarılı",by:isMini ? "jev" : "sol",summary,
      criteria:[{id:"K1",status:"met",status_tr:"Karşılandı",evidence:"Demo senaryosundaki davranış kontrolleri geçti."}],
      gaps:[], risks:["Bu rapor bir örnektir; gerçek model veya test çalıştırılmadı."], next_steps:["Jev'i yerel olarak kur ve --kuru ile gerçek ofis akışını dene."],
      html:"<h2>Demo koşusunun raporu</h2><p>Görev dağıtımı, doğrulama ve rapor akışı tamamlandı. Bu sayfa Jev'in gerçek ofis arayüzünü örnek verilerle gösterir.</p>",reports:[],fix_note:""};
    state.report = copy(report); state.reports.push({round:state.run.round,verdict:report.verdict,verdict_tr:report.verdict_tr,summary,by:report.by,ts:iso()});
    report.reports = copy(state.reports);
    reportArchive.set(report.round, copy(report));
    for (const a of agents) agent(a.name,"idle","",null,a.name === "jev" ? "orchestrator" : "worker");
    phase("reported"); emit("report.ready",{round:state.run.round,verdict:"basarili",by:report.by,summary});
  }
  const steps = [];
  const at = (n, fn) => steps.push({n,fn});
  at(1, () => {agent(isMini ? "jev" : "opus",isMini ? "thinking" : "planning",isMini ? "İşin boyunu ölçüyor" : "Planı ve görev kartlarını yazıyor",null,"plan"); emit("log",{level:"info",text:"Jev: " + (isMini ? "mini iş; tek ajan, plan yok." : "orta iş; kısa plan ve görev kartları.")});});
  at(3, () => {agent("opus","idle","",null,"plan"); phase("executing"); dispatch(tasks[0]);});
  if (isMini) {
    at(7, () => verify(tasks[0])); at(10, () => done(tasks[0]));
    at(12, () => {phase("reviewing"); agent("jev","verifying","Tek görevli işin kabul kontrolü",null,"orchestrator");}); at(15,finish);
  } else if (!retry) {
    at(5, () => dispatch(tasks[1])); at(8, () => verify(tasks[0])); at(10, () => done(tasks[0]));
    at(12, () => verify(tasks[1])); at(14, () => done(tasks[1])); at(16, () => dispatch(tasks[2]));
    at(20, () => verify(tasks[2])); at(22, () => done(tasks[2]));
    at(24, () => {phase("reviewing"); agent("sol","reviewing","Kanıt paketini ve sonucu inceliyor",null,"review"); agent("jev","verifying","Son doğrulamalar",null,"orchestrator");}); at(28,finish);
  } else {
    at(6, () => verify(tasks[0]));
    at(8, () => {const t=tasks[0];t.attempts.push({n:t.attempt,agent:t.agent,outcome:"verify_failed",outcome_tr:"Doğrulama başarısız",summary:"Silinen kayıt yeniden görünüyor",verify_pass:2,verify_total:3,duration_s:8}); update(t,"needs_decision"); agent("sol","failed","Silinen kayıt yeniden görünüyor",t.task_id); agent("jev","thinking","Kök nedeni değerlendiriyor",t.task_id,"orchestrator"); emit("verify",{task_id:t.task_id,passed:false,command:plan.commands.test});emit("log",{level:"warn",text:"T01: kayıt silme kontrolü başarısız; Jev karar verecek."});});
    at(10, () => {const d={decision:"retry",decision_tr:"Tekrar dene",agent:"sol",task_id:"T01",effort:"high",rationale:"Silme işlemi belleği güncelliyor ama dosyaya yazmıyor. Aynı ajan kayıt adımını düzeltip kontrolleri yineleyecek.",applied:"retry",applied_tr:"yeniden denenecek",deviation:"none"}; tasks[0].decisions.push(d); emit("jev.decision",d); dispatch(tasks[0]);});
    at(13, () => verify(tasks[0])); at(15, () => done(tasks[0]));
    at(17, () => dispatch(tasks[1])); at(19, () => dispatch(tasks[2]));
    at(22, () => {verify(tasks[1]); verify(tasks[2]);}); at(24, () => {done(tasks[1]); done(tasks[2]);});
    at(26, () => {phase("reviewing"); agent("sol","reviewing","Düzeltmenin kanıtlarını denetliyor",null,"review");}); at(30,finish);
  }
  const last = steps[steps.length-1].n;
  function step() {
    if (paused || tick >= last) return;
    tick++;
    for (const s of steps) if (s.n === tick) s.fn();
    state.server_now=iso(); state.progress.done=tasks.filter(t=>t.status==="done").length;
    state.progress.pct=Math.round(state.progress.done/tasks.length*100);
    syncToolbar();
    if (tick === last) {clearInterval(timer); timer=null;}
  }
  function schedule() {clearInterval(timer);timer=tick<last ? setInterval(step,2000/speed) : null;}
  const restart = () => {const u=new URL(location.href);u.searchParams.set("scenario",scenario);u.searchParams.set("speed",String(speed));u.searchParams.delete("step");u.searchParams.delete("paused");location.href=u.href;};
  function syncToolbar() {
    document.getElementById("demo-step").textContent=tick>=last ? "Tamamlandı · raporu aç" : paused ? "Oynatma duraklatıldı" : `${tick}/${last} · ${labels.phase[state.run.phase]}`;
    document.getElementById("demo-progress").max=last;document.getElementById("demo-progress").value=tick;
    document.getElementById("demo-pause").textContent=paused ? "Devam et" : "Duraklat";
    document.getElementById("demo-pause").disabled=tick>=last;
  }
  class DemoSource {
    constructor() {stream=this;this.readyState=1;setTimeout(()=>{if(this.onopen)this.onopen();schedule();},0);}
    addEventListener() {}
    close() {this.readyState=2;if(stream===this)stream=null;clearInterval(timer);}
  }
  const response = (data,status=200) => new Response(JSON.stringify(copy(data)),{status,headers:{"Content-Type":"application/json"}});
  async function localFetch(path, options={}) {
    const u=new URL(path,location.href), p=u.pathname;
    if(p === "/api/durum") {state.server_now=iso();return response(state);}
    if(p === "/api/plan") return response(plan);
    if(p === "/api/rapor") {const selected=reportArchive.get(Number(u.searchParams.get("tur")))||report;return response(selected ? {...selected,reports:state.reports} : {ok:false,mesaj:"Henüz rapor yok."});}
    const match=/^\/api\/ajan\/([^/]+)\/log$/.exec(p);
    if(match) {const a=agents.find(a=>a.name===match[1]);if(!a)return response({ok:false},404);
      return response({...a,ok:true,lines:history.filter(e=>e.data.agent===a.name).map(e=>({seq:e.seq,ts:e.ts,kind:e.data.state,text:e.data.text||e.data.rationale||e.data.title||"",task_id:e.data.task_id})),
        decisions:a.name==="jev" ? state.decisions.map(e=>({...e.data,ts:e.ts})) : [],attempts:tasks.filter(t=>t.agent===a.name).flatMap(t=>t.attempts.map(a=>({...a,task_id:t.task_id}))),calls:[]});}
    if(p === "/api/komut/duzelt" && options.method==="POST") {
      let body;try{body=JSON.parse(options.body||"{}");}catch{return response({ok:false,mesaj:"Geçersiz not."},400);}
      if(state.run.phase!=="reported")return response({ok:false,mesaj:"Önce raporu bekle."},409);
      if(!String(body.note||body.not||"").trim())return response({ok:false,mesaj:"Örnek düzeltme için kısa bir not yaz."},400);
      state.run.round++; phase("fixing");agent("opus","planning","Düzeltme notunu inceliyor",null,"fix");
      emit("log",{level:"info",text:"Demo: düzeltme isteği alındı. Gerçek dosya veya model çağrısı yapılmaz."});
      setTimeout(()=>{phase("reviewing");agent("sol","reviewing","Örnek düzeltmeyi inceliyor",null,"review");},1500);
      setTimeout(finish,3500);return response({ok:true});
    }
    return response({ok:false,mesaj:"Bu işlem demo senaryosunda desteklenmiyor."},404);
  }
  window.JevDemo={fetch:localFetch,EventSource:DemoSource};
  document.getElementById("demo-github").href=config.repository_url;
  document.getElementById("demo-scenario").value=scenario;
  document.getElementById("demo-scenario").addEventListener("change",e=>{const u=new URL(location.href);u.searchParams.set("scenario",e.target.value);u.searchParams.delete("step");u.searchParams.delete("paused");location.href=u.href;});
  document.getElementById("demo-speed").value=String(speed);
  document.getElementById("demo-speed").addEventListener("change",e=>{speed=Number(e.target.value);state.run.speed=speed;if(tick<last)schedule();});
  document.getElementById("demo-pause").addEventListener("click",()=>{paused=!paused;syncToolbar();});
  document.getElementById("demo-restart").addEventListener("click",restart);
  // Optional initial frame is useful for documentation screenshots and repeatable smoke checks.
  const initial=Math.min(last,Math.max(0,Math.floor(Number(params.get("step"))||0)));
  const initialPaused=paused;paused=false;for(let i=0;i<initial;i++)step();paused=initialPaused;
  syncToolbar();
})();
