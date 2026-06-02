package com.feihe.rdo.entity.cdm;


import com.fasterxml.jackson.annotation.JsonProperty;
import com.feihe.annotation.Derive;
import com.feihe.annotation.OdpsTable;
import com.feihe.util.PartitionUtil;
import lombok.AllArgsConstructor;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.time.LocalDateTime;

@Data
@AllArgsConstructor
@NoArgsConstructor
@OdpsTable(project = "firmus_dataphin_prd_cdm", table = "dim_shop")
public class DimShop {
    @Derive("code")
    private String keyId;

    /** 主键 */
    private String mdmPk;

    /** 父数据主键（父mdm_code） */
    private String mdmParentcode;

    /** 主数据编码 */
    private String mdmCode;

    /** 数据状态 */
    private Long mdmStatus;

    /** 合并状态 */
    private Long mdmDuplicate;

    /** 封存状态 */
    private Long mdmSeal;

    /** 创建数据来源 */
    private String mdmCreatedbyType;

    /** 创建者 */
    private String mdmCreatedby;

    /** 创建日期 */
    private String mdmCreatedon;

    /** 变更数据来源 */
    private String mdmModifiedbyType;

    /** 变更者 */
    private String mdmModifiedby;

    /** 变更日期 */
    private String mdmModifiedon;

    /** 名称 */
    private String name;

    /** 编码 */
    private String code;

    /** 描述 */
    private String description;

    /** 店铺简称 */
    private String shortnames;

    /** lev0_code */
    private String lev0Code;

    /** lev0_name */
    private String lev0Name;

    /** lev1_code */
    private String lev1Code;

    /** lev1_name */
    private String lev1Name;

    /** lev2_code */
    private String lev2Code;

    /** lev2_name */
    private String lev2Name;

    /** lev3_code */
    private String lev3Code;

    /** lev3_name */
    private String lev3Name;

    /** lev4_code */
    private String lev4Code;

    /** lev4_name */
    private String lev4Name;

    /** lev5_code */
    private String lev5Code;

    /** lev5_name */
    private String lev5Name;

    /** 市场区域 */
    private String organizecity;

    /** 系统南北区划分 */
    private String organizecityRegion;

    /** 业务南北区划分 */
    private String rateRegion;

    /** 行政城市 */
    private String officalcity;

    /** 地址 */
    private String address;

    /** 邮编 */
    private String postcode;

    /** 手机号 */
    private String mobile;

    /** 经营产品分类 */
    private String productsClassificy;

    /** 活跃标志 */
    private String activeflag;

    /** 最新开始合作时间 */
    private String opendate;

    /** 最新停止合作时间 */
    private String closedate;

    /** 首次开始合作时间 */
    private String firstOpendate;

    /** 首次停止合作时间 */
    private String firstClosedate;

    /** 门店业务归属 */
    private String pmproperty;

    /** 门店业务归属枚举值 */
    private String pmpropertyName;

    /** 经销商编号 */
    private String dealer;

    /** 经销商名称 */
    private String dealerName;

    /** 门店与经销商关系 */
    private String supplierrelationship;

    /** 门店与经销商关系枚举值 */
    private String supplierrelationshipName;

    /** 连锁店名称 */
    private String chainstoreName;

    /** 门店类别 */
    private String shopType;

    /** 门店类别枚举值 */
    private String shopTypeName;

    /** 业务类型 */
    private String busiType;

    /** 业务类型枚举值 */
    private String busiTypeName;

    /** 市场类型 */
    private String marketType;

    /** 市场类型枚举值 */
    private String marketTypeName;

    /** 门店渠道等级 */
    private String channelType;

    /** 门店渠道等级枚举值 */
    private String channelTypeName;

    /** 门店渠道属性 */
    private String channel;

    /** 门店渠道属性枚举值 */
    private String channelName;

    /** 门店分类 */
    private String classify;

    /** 门店分类枚举值 */
    private String classifyName;

    /** 门店等级 */
    private String cmlevel;

    /** 门店等级枚举值 */
    private String cmlevelName;

    /** 营业面积 */
    private String businessArea;

    /** 奶粉月容量 */
    private String analysis;

    /** 收银柜台数量 */
    private String counterNumber;

    /** 货架节数 */
    private String shelvesNumber;

    /** 商圈 */
    private String tradingarea;

    /** 经度 */
    private String longitude;

    /** 纬度 */
    private String latitude;

    /** 提成归属 */
    private String attribution;

    /** 是否裸价 */
    private String isbareprice;

    /** 1+N+X */
    private String oneornorx;

    /** 1+N+X枚举值 */
    private String oneornorxName;

    /** 是否主推门店 */
    private String isMainPush;

    /** 是否总仓 */
    private String isWarehouse;

    /** 录入时间 */
    private String entryTime;

    /** 机构 */
    private String actOrganize;

    /** 机构名称 */
    private String actOrganizeName;

    /** 系统字段(未知) */
    private String nutritionBusType;

    /** 虚拟门店类型 */
    private String storeType;

    /** 虚拟门店类型名称 */
    private String storeTypeName;

    /** 门店级别 */
    private String nutritionStoreLevel;

    /** 门店级别枚举值 */
    private String nutritionStoreLevelName;

    /** 新客类型 */
    private String newCustomersType;

    /** 新客类型枚举值 */
    private String newCustomersTypeName;

    /** 是否引流门店 */
    private String isDrainageShop;

    /** 门店顾问类型 */
    private String distributorStoreType;

    /** 门店顾问类型枚举值 */
    private String distributorStoreTypeName;

    /** 妈妈爱重点系统 */
    private String momLoveKeySystem;

    /** 妈妈爱重点系统枚举值 */
    private String momLoveKeySystemName;

    /** TOP3系统 */
    private String top3System;

    /** TOP3系统枚举值 */
    private String top3SystemName;

    /** 启用状态 */
    private String enablestate;

    /** 系统字段(未知) */
    private String makedept;

    /** 系统字段(未知) */
    private String makedate;

    /** 系统字段(未知) */
    private String frmstate;

    /** 系统字段(未知) */
    private String audituser;

    /** 系统字段(未知) */
    private String auditdate;

    /** 系统字段(未知) */
    private String makenum;

    /** 系统字段(未知) */
    private String makeuser;

    /** 系统字段(未知) */
    private String frmtitile;

    /** 数据更新时间 */
    private String ts;

    /** 数据删除标志 */
    private String dr;

    /** 增删标识 */
    private String busiFlag;

    /** 业务数据变动时间 */
    private String busiDate;

    /** 增量数据批量写入时间 */
    private LocalDateTime etlTime;

    /** 业务代表 */
    @JsonProperty("shop_leader_1")
    private String shopLeader1;

    /** 地区经理 */
    @JsonProperty("shop_leader_2")
    private String shopLeader2;

    /** 导购专员 */
    @JsonProperty("shop_leader_3")
    private String shopLeader3;

    /** 连锁店编码 */
    private String chainstoreCode;

    /** 业务代表编号 */
    @JsonProperty("shop_leader_id_1")
    private String shopLeaderId1;

    /** 地区经理编号 */
    @JsonProperty("shop_leader_id_2")
    private String shopLeaderId2;

    /** 导购专员编号 */
    @JsonProperty("shop_leader_id_3")
    private String shopLeaderId3;

    /** 总仓类型code(关联mdm_code_warehouse_type) */
    private String generalWarehouseTypeCode;

    /** 总仓类型名称 */
    private String generalWarehouseTypeName;

    /** 婴童渠道类型code(关联mdm_code_babychannel_type) */
    private String babyChannelTypeCode;

    /** 婴童渠道类型名称 */
    private String babyChannelTypeName;

    /** 新零售门店(0:其他;1:是;2:否) */
    private String newRetailStore;

    /** 是否新零售门店 */
    private String newRetailStoreName;

    /** KSC主管code */
    private String shopKscId;

    /** NKA主管code */
    private String shopNkaId;

    /** KSC主管name */
    private String shopKscName;

    /** NKA主管name */
    private String shopNkaName;

    /** 行政省区编码 */
    private String provinceCode;

    /** 行政省区 */
    private String provinceName;

    /** 行政市区编码 */
    private String cityCode;

    /** 行政市区 */
    private String cityName;

    /** 行政区县编码 */
    private String districtCode;

    /** 行政区县 */
    private String districtName;

    /** 街道编码 */
    private String villageCode;

    /** 村镇街道 */
    private String villageName;

    /** 操作方式 */
    private String operateMode;

    /** 是否经营成人粉 */
    private String saleAdultPowder;

    /** 是否星耀联盟 */
    private String isStarUnion;

    /** 是否返利门店 */
    private String isControlStore;

    /** 是否跨区域门店 */
    private String isCrossRegionalSupply;

    /** 星耀联盟 */
    private String starlightAlliance;

    /** 否为大日期集中区划门店 */
    private String lDateCentralizedShop;

    /** 归属省 */
    private String province;

    /** 归属市 */
    private String city;

    /** 归属区 */
    private String district;

    /** 归属市级别 */
    private String level;

    /** 虚拟门店业务类型名称 */
    private String nutritionBusTypeName;

    /** 爆量终端CODE */
    private String burstTerminalCode;

    /** 爆量终端NAME */
    private String burstTerminalName;

    /** 成人粉月容量 */
    private String adultPowderCapacity;

    /** 是否核销代金券门店 */
    private String isVoucherStore;

    /** 是否为促进费转货款门店 */
    private String isWhiteList;

    /** 是否为TOP3系统 */
    private String isTop3System;

    /** 是否为臻稚高质量门店 */
    private String isHighQuality;

    /** 大系统渠道编号 */
    private String bigSystemChannelCode;

    /** 大系统渠道名称 */
    private String bigSystemChannelName;

    /** 门店联系人 */
    private String storeContracter;

    /** 门店联系人手机号 */
    private String storeContracterMobile;

    /** 最新导购专员 */
    @JsonProperty("shop_leader_3_last")
    private String shopLeader3Last;

    /** 最新导购专员编号 */
    @JsonProperty("shop_leader_id_3_last")
    private String shopLeaderId3Last;

    /** 最新业务代表 */
    @JsonProperty("shop_leader_1_last")
    private String shopLeader1Last;

    /** 最新业务代表编号 */
    @JsonProperty("shop_leader_id_1_last")
    private String shopLeaderId1Last;

    /** 最新地区经理 */
    @JsonProperty("shop_leader_2_last")
    private String shopLeader2Last;

    /** 最新地区经理编号 */
    @JsonProperty("shop_leader_id_2_last")
    private String shopLeaderId2Last;

    /** 最新KSC主管 */
    private String shopKscNameLast;

    /** 最新KSC主管编号 */
    private String shopKscIdLast;

    /** 最新NKA主管 */
    private String shopNkaNameLast;

    /** 最新NKA主管编号 */
    private String shopNkaIdLast;

    /** 经销商mdm编码 */
    private String dealerMdmCode;

    /** 其他门店负责人编号 */
    private String shopLeaderIdOther;

    /** 其他门店负责人 */
    private String shopLeaderOther;

    /** 最新其他门店负责人编号 */
    private String shopLeaderIdOtherLast;

    /** 最新其他门店负责人 */
    private String shopLeaderOtherLast;

    /** 是否数字化门店 */
    private String isDigitized;

    /** 是否地方型重点系统 */
    private String isLocalKeySystem;

    /** G2重点母婴系统 */
    private String g2MotherChild;

    /** 数字化门店标签 */
    private String digitizedLabel;

    /** 连锁系统分公司 */
    private String systemSubsidiary;

    /** 是否参与终端利益绑定(1:参与/展示、2:不参与、3:参与/不展示) */
    private String isBenefitBinding;

    /** 加盟方式编码 */
    private String franchiseMethodCode;

    /** 加盟方式名称 */
    private String franchiseMethodName;

    /** 加盟系统编码 */
    private String franchiseSystemCode;

    /** 加盟系统名称 */
    private String franchiseSystemName;

    /** 加盟类型编码 */
    private String franchiseTypeCode;

    /** 加盟类型名称 */
    private String franchiseTypeName;

    /** 加盟年月 */
    private String franchiseMonth;

    /** 是否POS打通 */
    private String isPosIntergrated;

    /** 是否虚拟门店/虚拟总仓 */
    private String isVirtualShopVirtualZc;

    /** 大系统编码 */
    private String bigSysCode;

    /** 大系统名称 */
    private String bigSysName;

    /** 是否当月活跃 */
    private String isCurrentMonthActive;

    /** 补贴站 1-非补贴站，2-当前为补贴站，3-已取消-有名额使用 */
    private String subsidyStation;

    /** 门店与连锁系统关系 */
    private String shopGiveChainstoreRelationship;

    /** 价值链类型 */
    private String valueChainType;

    /** 价值链类型名称 */
    private String valueChainTypeName;

    /** 是否备案参与提成包核算编码 */
    private String isNewValueChain;

    /** 是否备案参与提成包核算 */
    private String isFilingsJoinCommissionBaoAccounting;

    /** 连锁系统计算编码 */
    private String chainstoreCalculateCode;

    /** 连锁系统计算名称 */
    private String chainstoreCalculateName;

    public static final String QUERY_SQL = "select  mdm_pk\n"
            + "       ,mdm_parentcode\n"
            + "       ,mdm_code\n"
            + "       ,mdm_status\n"
            + "       ,mdm_duplicate\n"
            + "       ,mdm_seal\n"
            + "       ,mdm_createdby_type\n"
            + "       ,mdm_createdby\n"
            + "       ,mdm_createdon\n"
            + "       ,mdm_modifiedby_type\n"
            + "       ,mdm_modifiedby\n"
            + "       ,mdm_modifiedon\n"
            + "       ,name\n"
            + "       ,code\n"
            + "       ,description\n"
            + "       ,shortnames\n"
            + "       ,lev0_code\n"
            + "       ,lev0_name\n"
            + "       ,lev1_code\n"
            + "       ,lev1_name\n"
            + "       ,lev2_code\n"
            + "       ,lev2_name\n"
            + "       ,lev3_code\n"
            + "       ,lev3_name\n"
            + "       ,lev4_code\n"
            + "       ,lev4_name\n"
            + "       ,lev5_code\n"
            + "       ,lev5_name\n"
            + "       ,organizecity\n"
            + "       ,organizecity_region\n"
            + "       ,rate_region\n"
            + "       ,officalcity\n"
            + "       ,address\n"
            + "       ,postcode\n"
            + "       ,mobile\n"
            + "       ,products_classificy\n"
            + "       ,activeflag\n"
            + "       ,opendate\n"
            + "       ,closedate\n"
            + "       ,first_opendate\n"
            + "       ,first_closedate\n"
            + "       ,pmproperty\n"
            + "       ,pmproperty_name\n"
            + "       ,dealer\n"
            + "       ,dealer_name\n"
            + "       ,supplierrelationship\n"
            + "       ,supplierrelationship_name\n"
            + "       ,chainstore_name\n"
            + "       ,shop_type\n"
            + "       ,shop_type_name\n"
            + "       ,busi_type\n"
            + "       ,busi_type_name\n"
            + "       ,market_type\n"
            + "       ,market_type_name\n"
            + "       ,channel_type\n"
            + "       ,channel_type_name\n"
            + "       ,channel\n"
            + "       ,channel_name\n"
            + "       ,classify\n"
            + "       ,classify_name\n"
            + "       ,cmlevel\n"
            + "       ,cmlevel_name\n"
            + "       ,business_area\n"
            + "       ,analysis\n"
            + "       ,counter_number\n"
            + "       ,shelves_number\n"
            + "       ,tradingarea\n"
            + "       ,longitude\n"
            + "       ,latitude\n"
            + "       ,attribution\n"
            + "       ,isbareprice\n"
            + "       ,oneornorx\n"
            + "       ,oneornorx_name\n"
            + "       ,is_main_push\n"
            + "       ,is_warehouse\n"
            + "       ,entry_time\n"
            + "       ,act_organize\n"
            + "       ,act_organize_name\n"
            + "       ,nutrition_bus_type\n"
            + "       ,store_type\n"
            + "       ,store_type_name\n"
            + "       ,nutrition_store_level\n"
            + "       ,nutrition_store_level_name\n"
            + "       ,new_customers_type\n"
            + "       ,new_customers_type_name\n"
            + "       ,is_drainage_shop\n"
            + "       ,distributor_store_type\n"
            + "       ,distributor_store_type_name\n"
            + "       ,mom_love_key_system\n"
            + "       ,mom_love_key_system_name\n"
            + "       ,top3_system\n"
            + "       ,top3_system_name\n"
            + "       ,enablestate\n"
            + "       ,makedept\n"
            + "       ,makedate\n"
            + "       ,frmstate\n"
            + "       ,audituser\n"
            + "       ,auditdate\n"
            + "       ,makenum\n"
            + "       ,makeuser\n"
            + "       ,frmtitile\n"
            + "       ,ts\n"
            + "       ,dr\n"
            + "       ,busi_flag\n"
            + "       ,busi_date\n"
            + "       ,etl_time\n"
            + "       ,shop_leader_1\n"
            + "       ,shop_leader_2\n"
            + "       ,shop_leader_3\n"
            + "       ,chainstore_code\n"
            + "       ,shop_leader_id_1\n"
            + "       ,shop_leader_id_2\n"
            + "       ,shop_leader_id_3\n"
            + "       ,general_warehouse_type_code\n"
            + "       ,general_warehouse_type_name\n"
            + "       ,baby_channel_type_code\n"
            + "       ,baby_channel_type_name\n"
            + "       ,new_retail_store\n"
            + "       ,new_retail_store_name\n"
            + "       ,shop_ksc_id\n"
            + "       ,shop_nka_id\n"
            + "       ,shop_ksc_name\n"
            + "       ,shop_nka_name\n"
            + "       ,province_code\n"
            + "       ,province_name\n"
            + "       ,city_code\n"
            + "       ,city_name\n"
            + "       ,district_code\n"
            + "       ,district_name\n"
            + "       ,village_code\n"
            + "       ,village_name\n"
            + "       ,operate_mode\n"
            + "       ,sale_adult_powder\n"
            + "       ,is_star_union\n"
            + "       ,is_control_store\n"
            + "       ,is_cross_regional_supply\n"
            + "       ,starlight_alliance\n"
            + "       ,l_date_centralized_shop\n"
            + "       ,province\n"
            + "       ,city\n"
            + "       ,district\n"
            + "       ,level\n"
            + "       ,nutrition_bus_type_name\n"
            + "       ,burst_terminal_code\n"
            + "       ,burst_terminal_name\n"
            + "       ,adult_powder_capacity\n"
            + "       ,is_voucher_store\n"
            + "       ,is_white_list\n"
            + "       ,is_top3_system\n"
            + "       ,is_high_quality\n"
            + "       ,big_system_channel_code\n"
            + "       ,big_system_channel_name\n"
            + "       ,store_contracter\n"
            + "       ,store_contracter_mobile\n"
            + "       ,shop_leader_3_last\n"
            + "       ,shop_leader_id_3_last\n"
            + "       ,shop_leader_1_last\n"
            + "       ,shop_leader_id_1_last\n"
            + "       ,shop_leader_2_last\n"
            + "       ,shop_leader_id_2_last\n"
            + "       ,shop_ksc_name_last\n"
            + "       ,shop_ksc_id_last\n"
            + "       ,shop_nka_name_last\n"
            + "       ,shop_nka_id_last\n"
            + "       ,dealer_mdm_code\n"
            + "       ,shop_leader_id_other\n"
            + "       ,shop_leader_other\n"
            + "       ,shop_leader_id_other_last\n"
            + "       ,shop_leader_other_last\n"
            + "       ,is_digitized\n"
            + "       ,is_local_key_system\n"
            + "       ,g2_mother_child\n"
            + "       ,digitized_label\n"
            + "       ,system_subsidiary\n"
            + "       ,is_benefit_binding\n"
            + "       ,franchise_method_code\n"
            + "       ,franchise_method_name\n"
            + "       ,franchise_system_code\n"
            + "       ,franchise_system_name\n"
            + "       ,franchise_type_code\n"
            + "       ,franchise_type_name\n"
            + "       ,franchise_month\n"
            + "       ,is_pos_intergrated\n"
            + "       ,is_virtual_shop_virtual_zc\n"
            + "       ,big_sys_code\n"
            + "       ,big_sys_name\n"
            + "       ,is_current_month_active\n"
            + "       ,subsidy_station\n"
            + "       ,shop_give_chainstore_relationship\n"
            + "       ,value_chain_type\n"
            + "       ,value_chain_type_name\n"
            + "       ,is_new_value_chain\n"
            + "       ,is_filings_join_commission_bao_accounting\n"
            + "       ,chainstore_calculate_code\n"
            + "       ,chainstore_calculate_name\n"
            + "from    firmus_dataphin_prd_cdm.dim_shop\n"
            + "where " + PartitionUtil.sqlBizDate() + ";";
}
