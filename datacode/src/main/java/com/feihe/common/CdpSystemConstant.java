package com.feihe.common;

import java.lang.management.ManagementFactory;

public class CdpSystemConstant {
    private CdpSystemConstant() {
    }

    // 空间信息属于企业 数据结构需要调整
    private static final String DEV_WORKSPACE_ID = "100820260316170435000001";
    private static final String PROD_WORKSPACE_ID = "100820260316170503000002";

    public static final String API_PREFIX = "/api/gateway/v1";

    public static String getWorkspaceId() {
        if (ManagementFactory.getOperatingSystemMXBean().getName().startsWith("Mac")) {
            return DEV_WORKSPACE_ID;
        } else {
            return PROD_WORKSPACE_ID;
        }
    }
    public static String getLogDomain(){
        return System.getenv("CDP_LOG_DOMAIN");
    }

    public static String getDataPlatformKeyId() {
        return System.getenv("CDP_DATA_PLATFORM_KEY_ID");
    }

    public static String getDataPlatformKey() {
        return System.getenv("CDP_DATA_PLATFORM_KEY");
    }

    public static String getDoMain() {
        return System.getenv("CDP_DOMAIN");
    }

    public static String getCorporationId() {
        return System.getenv("CDP_CORPORATION_ID");
    }
}
