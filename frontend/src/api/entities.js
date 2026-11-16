import {api} from './client.js';
export const datasets={list:()=>api('/datasets'),get:id=>api('/datasets/'+id),create:data=>api('/datasets',{method:'POST',body:JSON.stringify(data)})};
export const jobs={list:()=>api('/jobs'),get:id=>api('/jobs/'+id),create:data=>api('/jobs',{method:'POST',body:JSON.stringify(data)}),approve:id=>api('/jobs/'+id+'/approve',{method:'POST'})};
export const reports={list:()=>api('/reports'),create:data=>api('/reports',{method:'POST',body:JSON.stringify(data)}),toggle:id=>api('/reports/'+id+'/toggle',{method:'POST'}),deliveries:()=>api('/deliveries')};
export const intakes={
 list:()=>api('/intakes'),
 get:id=>api('/intakes/'+id),
 create:data=>api('/intakes',{method:'POST',body:JSON.stringify(data)}),
 replay:(id,rowId,corrections,expected_seq)=>api('/intakes/'+id+'/rows/'+rowId+'/replay',{method:'POST',body:JSON.stringify({corrections,expected_seq})}),
 adjudicate:(id,rowId,decision,note,expected_seq)=>api('/intakes/'+id+'/rows/'+rowId+'/adjudicate',{method:'POST',body:JSON.stringify({decision,note,expected_seq})}),
 reconcile:id=>api('/intakes/'+id+'/reconcile',{method:'POST'}),
 settle:(id,expected_seq)=>api('/intakes/'+id+'/settle',{method:'POST',body:JSON.stringify({expected_seq})}),
 conventions:()=>api('/intakes/conventions'),
 createConvention:data=>api('/intakes/conventions',{method:'POST',body:JSON.stringify(data)})
};
