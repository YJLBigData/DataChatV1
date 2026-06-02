package com.feihe.rdo.behavior.cdm;


import com.feihe.annotation.Account;
import com.feihe.annotation.BehaviorEvent;
import com.feihe.annotation.EventCode;
import com.feihe.annotation.EventTime;
import com.feihe.annotation.OdpsTable;
import com.feihe.util.PartitionUtil;
import lombok.AllArgsConstructor;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.time.LocalDateTime;

@Data
@AllArgsConstructor
@NoArgsConstructor
@BehaviorEvent
@OdpsTable(project = "firmus_dataphin_prd_cdm", table = "fct_trd_member_actual_sale_order_detail_df")
public class FctCustomerCarePwaTaskDetail {
    public static final String APP_KEY = "1SERVE06WLDAPGS12N";
    @EventCode
    public String eventCode;
    @EventTime
    private LocalDateTime createTime;
    private String sonTaskTypeCode;
    private String sonTaskTypeName;
    private String shopCode;
    private String mainTaskTypeCode;
    private String mainTaskTypeName;
    @Account
    private String businessId;
    public static final String QUERY_SQL = "select  create_time\n"
            + "       ,son_task_type_code\n"
            + "       ,son_task_type_name\n"
            + "       ,shop_code\n"
            + "       ,main_task_type_code\n"
            + "       ,main_task_type_name\n"
            + "       ,member_id as business_id\n"
            + "       ,'GuidesCare_Task_Issued_Success' as event_code\n"
            + "from    firmus_dataphin_prd_cdm.fct_customer_care_pwa_task_detail\n"
            + "where " + PartitionUtil.sqlBizDate() + "\n"
            + "limit 5000000;";
}
