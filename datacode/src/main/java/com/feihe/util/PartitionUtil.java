package com.feihe.util;

import com.feihe.common.PartitionConstant;


public class PartitionUtil {
    private PartitionUtil() {
    }

    public static String bizDateEq(String partition) {
        return PartitionConstant.DS_PARTITION_EQUALS.concat(partition);
    }

    public static String bizDate() {
        return DateUtil.yesterday();
    }


    public static String sqlBizDate() {
        return sqlBizDate(DateUtil.yesterday());
    }

    public static String sqlBizDate(String partition) {
        return PartitionConstant.DS_PARTITION_EQUALS.concat("'").concat(partition).concat("'");
    }
}
