import {intakes} from '../api/entities.js';
import {workspace} from '../stores/workspace.js';
import {session} from '../stores/session.js';
import {heading} from '../components/common/shell.js';
import {esc,notify} from '../utils/dom.js';
import {submit} from '../hooks/forms.js';
import {parseCSV} from '../utils/csv.js';
import {IntakeStatus,RowStatus,ReasonCode,RowResolution} from '../constants/enums.js';

const badge=(value,label)=>'<span class="pill '+esc(value)+'">'+esc(label)+'</span>';
const lockedField=f=>esc(f.label)+'（'+esc(f.key)+' · '+esc(f.type)+(f.min!=null||f.max!=null?' · '+esc(f.min??'—')+' 至 '+esc(f.max??'—'):'')+'）';

function rawValue(row,field){
 const raw=row.raw||{};
 if(Object.prototype.hasOwnProperty.call(raw,field.key))return raw[field.key];
 for(const [k,v] of Object.entries(raw)){
  if([field.key,...(field.aliases||[])].some(a=>String(a).toLowerCase()===String(k).toLowerCase()))return v;
 }
 return '';
}

function rowActions(row,sheet,canWrite,isAdmin,editable){
 if(row.status==='accepted')return '<span class="hint">已接纳，不再写入批次</span>';
 if(!editable)return '<span class="hint">清理单已结清，去向定稿不可改</span>';
 const fields=sheet.convention.fields;
 const inputs=fields.map(f=>'<label class="mini">'+esc(f.key)+'<input name="c-'+esc(f.key)+'" value="'+esc(row.draft?.[f.key]??rawValue(row,f))+'"></label>').join('');
 const replay='<form class="row-form" data-replay="'+row.id+'">'+inputs+'<button class="button small" type="submit" '+(canWrite?'':'disabled')+'>修正并重放</button></form>';
 const adjudicate='<form class="row-form" data-adjudicate="'+row.id+'"><label class="mini">裁决说明<input name="note" minlength="2" maxlength="300" placeholder="'+(row.needs_adjudication?'与已接纳件重复，保留首件':'供应商确认本月不纳入')+'"></label><button class="text-button" name="decision" value="excluded" '+(isAdmin?'':'disabled')+'>裁决排除</button><button class="text-button" name="decision" value="retained" '+(isAdmin?'':'disabled')+'>暂缓保留</button>'+(row.needs_adjudication?'<span class="hint">资产冲突需负责人裁决</span>':'')+'</form>';
 return replay+adjudicate;
}

function sheetRows(sheet,canWrite,isAdmin,editable){
 return '<div class="table-wrap"><table><thead><tr><th>原行</th><th>原始内容</th><th>去向</th><th>原因</th><th>修正/裁决</th><th>批次位置</th><th>操作</th></tr></thead><tbody>'+
  sheet.rows.map(row=>'<tr class="row-'+esc(row.status)+'"><td>'+(row.row_index+1)+'</td><td><code>'+esc(JSON.stringify(row.raw))+'</code></td><td>'+badge(row.status,RowStatus[row.status])+(row.status==='accepted'?'':'<br>'+badge(row.resolution,RowResolution[row.resolution]))+'</td><td>'+(row.status==='accepted'?'—':('<small class="reason-code">'+esc(ReasonCode[row.reason_code]||row.reason_code)+'</small> '+esc(row.reason)))+(row.resolution_note?'<br><small>裁决：'+esc(row.resolution_note)+'</small>':'')+'</td><td>'+(row.normalized?'<code>'+esc(JSON.stringify(row.normalized))+'</code>':(row.draft&&Object.keys(row.draft).length?'<small>修正：'+esc(JSON.stringify(row.draft))+'</small>':''))+'</td><td>'+(row.batch_position??'—')+'</td><td class="actions">'+rowActions(row,sheet,canWrite,isAdmin,editable)+'</td></tr>').join('')+
  '</tbody></table></div>';
}

function reconcilePanel(report){
 if(!report)return '<p class="hint">点击“核对批次去向”，逐项检查原行 = 接纳 + 隔离、批次记录与每个接纳件对应。</p>';
 return '<div class="reconcile"><div class="numbers">'+[['原始行',report.counts.raw],['接纳',report.counts.accepted],['隔离',report.counts.quarantined],['待处理',report.counts.pending]].map(([k,v])=>'<div><span>'+k+'</span><strong>'+v+'</strong></div>').join('')+'</div>'+
  '<ul class="checks">'+report.checks.map(c=>'<li class="'+(c.ok?'ok':'bad')+'"><strong>'+(c.ok?'✓':'✗')+' '+esc(c.key)+'</strong><span>'+esc(c.detail)+'</span></li>').join('')+'</ul>'+
  (report.batch?'<p class="hint">可试算批次 #'+report.batch.id+' · '+esc(report.batch.name)+' · '+report.batch.rows+' 条 · SHA256 '+esc(report.batch.checksum.slice(0,18))+'…</p>':'')+
  (report.destinations.length?'<details><summary>逐件批次去向（'+report.destinations.length+'）</summary><div class="table-wrap"><table><thead><tr><th>原行</th><th>资产号</th><th>批次位置</th><th>批次记录</th><th>一致</th></tr></thead><tbody>'+report.destinations.map(d=>'<tr><td>'+(d.row_index+1)+'</td><td>'+esc(d.asset)+'</td><td>'+d.batch_position+'</td><td><code>'+esc(JSON.stringify(d.record))+'</code></td><td>'+(d.matches?'✓':'✗')+'</td></tr>').join('')+'</tbody></table></div></details>':'')+'</div>';
}

export async function intakePage(id){
 const [list,conventions]=await Promise.all([intakes.list(),intakes.conventions()]);
 workspace.intakes=list;
 const selected=id?await intakes.get(Number(id)):(list.length?await intakes.get(list[0].id):null);
 workspace.intakeDetail=selected;
 const showReconcile=workspace.intakeReport&&selected&&workspace.intakeReportSheet===selected.id?workspace.intakeReport:null;
 const canWrite=session.user.role!=='viewer',isAdmin=session.user.role==='admin';
 const m=workspace.meta;
 const pending=selected?.rows?.filter(r=>r.status==='quarantined'&&r.resolution==='pending')||[];
 const settled=selected?.status==='settled';
 return heading('02 / INTAKE CLEARING','接入清理单','受理时锁定原表与字段换算约定；逐行记录接纳或隔离原因。接纳件只写入一次可试算批次，隔离件可修正重放，已接纳件不被覆盖。')+
  '<div class="intake-grid"><section class="panel"><div class="section-title"><h2>清理单</h2><span>'+list.length+' 张</span></div><div class="batch-list">'+list.map(s=>'<button class="batch-item" data-sheet="'+s.id+'"><strong>'+esc(s.name)+' '+badge(s.status,IntakeStatus[s.status]||s.status)+'</strong><span>原行 '+s.row_count+' · 接纳 '+s.accepted_count+' · 隔离 '+s.quarantined_count+(s.pending_count?'（待处理 '+s.pending_count+'）':'')+'</span><small>约定 v'+s.convention_version+' · 序号 '+s.decision_seq+' · '+esc(s.checksum.slice(0,16))+'…</small></button>').join('')+(!list.length?'<p class="empty">尚无清理单。粘贴供应商月表或选择 CSV/JSON 文件开始受理。</p>':'')+'</div>'+
  '<div class="section-title"><h2>受理新表</h2><span>锁定约定 v'+(conventions[0]?.version||'—')+'</span></div>'+
  '<form id="intake-form"><label>清理单名称<input name="name" required minlength="2" placeholder="例如：11 月供应商压缩机月表"></label><label>来源<input name="source" maxlength="200" placeholder="供应商月表.csv"></label><label>上传 CSV 或 JSON<input type="file" id="intake-file" accept=".json,.csv"></label><label>原始行 JSON<textarea name="rows" rows="6" placeholder="粘贴 JSON 数组；CSV 的表头可用约定别名（资产号/里程/是否失效等）" required></textarea></label>'+
  '<label class="mini">重试幂等键（可选）<input name="idempotency_key" maxlength="80" placeholder="网络中断重试时填同一键，不重复建单"></label>'+
  '<button type="submit" class="primary" '+(canWrite?'':'disabled')+'>受理并逐行清理</button><p class="hint">字段约定：'+m.columns.map(c=>esc(c.key)+' / '+esc(c.label)).join('，')+'。约定在受理时锁定，之后更新约定不影响在途清理单。</p></form></section>'+
  '<section class="panel">'+(selected?
   '<div class="section-title"><h2>'+esc(selected.name)+' '+badge(selected.status,IntakeStatus[selected.status]||selected.status)+'</h2><span>来源 '+esc(selected.source||'—')+' · 决定序号 '+selected.decision_seq+'</span></div>'+
   '<div class="numbers four">'+[['原始行',selected.row_count],['接纳',selected.accepted_count],['隔离',selected.quarantined_count],['待处理隔离',selected.pending_count]].map(([k,v])=>'<div><span>'+k+'</span><strong>'+v+'</strong></div>').join('')+'</div>'+
   '<p class="hint">行数核对：'+selected.row_count+' = '+selected.accepted_count+' + '+selected.quarantined_count+'（每条原始行始终只归入接纳或隔离之一）。</p>'+
   (selected.batch_id?'<p class="hint">可试算批次 #'+selected.batch_id+' 已由接纳件生成；<a class="text-link" href="#/workbench">到工作台对该批次试算 ↗</a></p>':'<p class="hint">尚无接纳件，暂无可试算批次。</p>')+
   '<div class="method-sheet compact"><p class="eyebrow">LOCKED CONVENTION</p><h3>受理时锁定的字段换算约定 v'+selected.convention_version+'</h3><ul class="convention-list">'+selected.convention.fields.map(f=>'<li>'+lockedField(f)+'：别名 '+esc((f.aliases||[]).join(' / '))+(f.type==='flag'?'；真值 '+esc((f.true_values||[]).join('/'))+'，假值 '+esc((f.false_values||[]).join('/')):'')+'</li>').join('')+'<li>允许型号：'+esc((selected.convention.models||[]).join(' / ')||'不限')+'</li></ul><p class="hint">当前约定已到 v'+(conventions[0]?.version||'—')+'；更新约定只影响此后受理的新清理单，本单锁定在 v'+selected.convention_version+'，在途去向不变。</p>'+(isAdmin?'<details><summary>更新字段换算约定（仅负责人）</summary><form id="convention-form"><label class="mini">约定名称<input name="name" required minlength="2" value="'+esc(conventions[0]?.name||'接入约定')+'"></label><label class="mini">允许型号（逗号分隔）<input name="models" value="'+esc((conventions[0]?.config?.models||[]).join(', '))+'"></label><button class="button small" type="submit">发布新约定版本</button></form></details>':'')+'</div>'+
   '<div class="toolbar"><button class="button" id="reconcile-sheet">核对批次去向 ↻</button><button class="button primary" id="settle-sheet" '+(isAdmin&&!settled&&!pending.length?'':'disabled')+'>结清清理单</button>'+(pending.length?'<span class="hint">尚有 '+pending.length+' 件隔离待处理，不能结清</span>':(settled?'<span class="hint">已结清，逐行去向已定稿</span>':''))+'</div>'+
   '<div id="reconcile-panel">'+reconcilePanel(showReconcile)+'</div>'+
   '<div class="section-title"><h2>逐行去向</h2><span>'+selected.rows.length+' 行 · 原行保留</span></div>'+
   sheetRows(selected,canWrite&&!settled,isAdmin&&!settled,!settled)
   :'<div class="empty">选择左侧一张清理单，或先受理新表。</div>')+'</section></div>';
}

export function bindIntakes(refresh,id){
 const fileInput=document.querySelector('#intake-file');
 if(fileInput)fileInput.addEventListener('change',async e=>{try{const file=e.target.files[0];if(!file)return;document.querySelector('[name=source]').value=file.name;const text=await file.text();const rows=file.name.toLowerCase().endsWith('.csv')?parseCSV(text):JSON.parse(text);document.querySelector('[name=rows]').value=JSON.stringify(rows,null,2);}catch(error){notify(error.message,true);}});
 const form=document.querySelector('#intake-form');
 if(form)submit(form,async f=>{let rows;try{rows=JSON.parse(f.get('rows'));}catch{throw new Error('原始行必须是 JSON 数组');}if(!Array.isArray(rows)||!rows.length)throw new Error('请提供至少一行原始行');const result=await intakes.create({name:f.get('name'),source:f.get('source'),rows,idempotency_key:f.get('idempotency_key')||undefined});workspace.intakeReport=null;notify(result.created?'已受理，逐行去向已记录':'该表已受理过，打开原清理单（未重复建单）');location.hash='#/intakes/'+result.sheet.id;await refresh();});
 document.querySelectorAll('[data-sheet]').forEach(button=>button.onclick=()=>{workspace.intakeReport=null;location.hash='#/intakes/'+button.dataset.sheet;});
 const conventionForm=document.querySelector('#convention-form');
 if(conventionForm)submit(conventionForm,async f=>{const latest=(await intakes.conventions())[0];const config=JSON.parse(JSON.stringify(latest.config));config.models=String(f.get('models')||'').split(',').map(v=>v.trim()).filter(Boolean);await intakes.createConvention({name:f.get('name'),config});notify('新约定已发布，仅影响此后受理的清理单');await refresh();});
 const sheet=workspace.intakeDetail;
 if(!sheet)return;
 const seq=()=>workspace.intakeDetail.decision_seq;
 document.querySelector('#reconcile-sheet')?.addEventListener('click',async()=>{try{workspace.intakeReport=await intakes.reconcile(sheet.id);workspace.intakeReportSheet=sheet.id;document.querySelector('#reconcile-panel').innerHTML=reconcilePanel(workspace.intakeReport);notify(workspace.intakeReport.ok?'批次去向核对通过':'核对未通过，请查看明细',!workspace.intakeReport.ok);}catch(e){notify(e.message,true);}});
 document.querySelector('#settle-sheet')?.addEventListener('click',async()=>{try{await intakes.settle(sheet.id,seq());notify('清理单已结清');workspace.intakeReport=null;workspace.intakeReportSheet=null;await refresh();}catch(e){notify(e.message,true);}});
 document.querySelectorAll('[data-replay]').forEach(el=>el.addEventListener('submit',async e=>{e.preventDefault();const button=el.querySelector('button[type=submit]');button.disabled=true;try{const corrections={};for(const field of sheet.convention.fields){const input=el.querySelector('[name=c-'+field.key+']');if(input&&input.value!=='')corrections[field.key]=input.value;}const result=await intakes.replay(sheet.id,Number(el.dataset.replay),corrections,seq());notify(result.row.status==='accepted'?'已修正并接纳，写入可试算批次':('仍隔离：'+result.row.reason),result.row.status!=='accepted');await refresh();}catch(error){notify(error.message,true);button.disabled=false;}}));
 document.querySelectorAll('[data-adjudicate]').forEach(el=>el.querySelectorAll('button[name=decision]').forEach(button=>button.addEventListener('click',async e=>{e.preventDefault();const note=el.querySelector('[name=note]').value.trim();if(note.length<2){notify('请填写至少两个字裁决说明',true);return;}button.disabled=true;try{await intakes.adjudicate(sheet.id,Number(el.dataset.adjudicate),button.value,note,seq());notify(button.value==='excluded'?'已裁决排除，原行保留':'已暂缓，仍需处理');await refresh();}catch(error){notify(error.message,true);button.disabled=false;}})));
}
