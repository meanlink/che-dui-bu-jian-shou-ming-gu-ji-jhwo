export const Role={admin:'业务负责人',analyst:'分析员',viewer:'观察员'};
export const JobStatus={queued:'排队中',running:'计算中',completed:'已完成',failed:'执行失败'};
export const IntakeStatus={open:'受理中',settled:'已结清'};
export const RowStatus={accepted:'接纳',quarantined:'隔离'};
export const ReasonCode={
 accepted:'已接纳',
 row_not_object:'整行不可读',
 missing_field:'缺少必填字段',
 invalid_number:'数值无效',
 invalid_flag:'失效标记无效',
 unknown_model:'型号不在约定内',
 asset_conflict:'资产号冲突'
};
export const RowResolution={pending:'待处理',accepted:'已接纳',excluded:'已裁决排除'};
