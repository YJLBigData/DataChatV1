package com.feihe.rdo.entity.cdm;


import com.feihe.annotation.CustomerEntity;
import com.feihe.annotation.Identity;
import com.feihe.annotation.OdpsTable;
import com.feihe.enumerate.CustomerIdentityType;
import com.feihe.util.PartitionUtil;
import lombok.AllArgsConstructor;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.time.LocalDateTime;

@Data
@AllArgsConstructor
@NoArgsConstructor
@CustomerEntity
@OdpsTable(project = "firmus_dataphin_prd_cdm", table = "dim_customer")
public class DimCustomer {
    /** 会员ID */
    @Identity(CustomerIdentityType.MEMBER_ID)
    private String memberId;

    /** 微信openid */
    private String wechatOpenid;
    /** 主键 */
    private String id;

    /** 会员名称 */
    private String memberName;

    /** 会员手机号 */
    private String mobile;

    /** 会员生日 */
    private LocalDateTime birthday;

    /** 会员邮箱 */
    private String email;

    /** 性别 */
    private String gender;

    /** 会员等级 */
    private String memberLevel;

    /** 会员身份 */
    private String identity;

    /** 会员微信昵称 */
    private String nickName;

    /** 微信号 */
    private String wechat;

    /** 首次关注时间 */
    private LocalDateTime firstFollowTime;

    /** 是否关注微信公众号 */
    private String isFollowWechat;

    /** 当前关注微信公众号时间 */
    private LocalDateTime followWechatTime;

    /** 当前取消关注微信公众号时间 */
    private LocalDateTime unfollowWechatTime;

    /** 会员创建时间 */
    private LocalDateTime createTime;

    /** 消费者类型 */
    private String memberType;

    /** 成为新客时间 */
    private LocalDateTime newTime;

    /** 成为老客时间 */
    private LocalDateTime oldTime;

    /** 注销时间 */
    private LocalDateTime logoutTime;

    /** 成为新客单号 */
    private String newOrder;

    /** 成为老客单号 */
    private String oldOrder;

    /** 是否注销 */
    private String isLogout;

    /** 一胎宝宝生日 */
    private String firstBabyBirthday;

    /** 二胎宝宝生日 */
    private String secondBabyBirthday;

    /** 三胎宝宝生日 */
    private String thirdBabyBirthday;

    /** 最小宝宝生日 */
    private String minimumBabyBirthday;

    /** 一胎宝宝性别 */
    private String firstBabyGender;

    /** 二胎宝宝性别 */
    private String secondBabyGender;

    /** 三胎宝宝性别 */
    private String thirdBabyGender;

    /** 最小宝宝性别 */
    private String minimumBabyGender;

    /** 会员来源身份 */
    private String memberScore;

    /** 生育阶段 */
    private String growthStage;

    /** 子女个数 */
    private Long babyNum;

    /** 平均月龄 */
    private Double babyAvgmonthage;

    /** 喂养方式 */
    private String feedWay;

    /** 备用字段1 */
    private String regChannel;

    /** 归属VIP */
    private String belongVip;

    /** 推荐人手机号 */
    private String referMobile;

    /** 是否销户 */
    private String isCanceled;

    /** 是否黑名单会员 */
    private String isBlack;

    /** 是否内部员工 */
    private String isInnerMember;

    /** 是否会员 */
    private String isMember;

    /** 是否删除 */
    private String isDelete;

    /** 一级关注渠道 */
    private String followChannel1;

    /** 二级关注渠道 */
    private String followChannel2;

    /** 三级关注渠道 */
    private String followChannel3;

    /** 四级关注渠道 */
    private String followChannel4;

    /** 五级关注渠道 */
    private String followChannel5;

    /** 六级关注渠道 */
    private String followChannel6;

    /** 一级新客渠道 */
    private String newChannel1;

    /** 二级新客渠道 */
    private String newChannel2;

    /** 三级新客渠道 */
    private String newChannel3;

    /** 四级新客渠道 */
    private String newChannel4;

    /** 一级老客渠道 */
    private String oldChannel1;

    /** 二级老客渠道 */
    private String oldChannel2;

    /** 三级老客渠道 */
    private String oldChannel3;

    /** 四级老客渠道 */
    private String oldChannel4;

    /** 星妈会一级注册渠道 */
    private String xmhRegChannel1;

    /** 星妈会二级注册渠道 */
    private String xmhRegChannel2;

    /** 星妈会三级注册渠道 */
    private String xmhRegChannel3;

    /** 星妈会四级注册渠道 */
    private String xmhRegChannel4;

    /** 注册时间 */
    private LocalDateTime regTime;

    /** 一胎宝宝姓名 */
    private String firstBabyName;

    /** 二胎宝宝姓名 */
    private String secondBabyName;

    /** 三胎宝宝姓名 */
    private String thirdBabyName;

    /** 会员等级编码 */
    private String memberLevelCode;

    /** 二胎新客来源 */
    private String secondCustomerFrom;

    /** 二胎新客订单 */
    private String secondNewOrder;

    /** 二胎新客时间 */
    private String secondNewTime;

    /** 三胎新客来源 */
    private String thirdCustomerFrom;

    /** 三胎新客订单 */
    private String thirdNewOrder;

    /** 三胎新客时间 */
    private String thirdNewTime;

    /** 备用字段9 */
    private String lastOrder;

    /** 备用字段8 */
    private LocalDateTime lastTime;

    /** 备用字段10 */
    private String lastChannel1;

    /** 备用字段11 */
    private String lastChannel2;

    /** 备用字段12 */
    private String lastChannel3;

    /** 备用字段13 */
    private String lastChannel4;

    /** 男孩个数 */
    private Long boyBabys;

    /** 女孩个数 */
    private Long girlBabys;

    /** 最小宝宝月龄 */
    private Long minimumBabyMonthAge;

    /** 归属新客门店code */
    private String belongStoreCode;

    /** 月龄定制时间 */
    private String customizedMonthageTime;

    /** 星妈会五级注册渠道 */
    private String xmhRegChannel5;

    /** 星妈会六级注册渠道 */
    private String xmhRegChannel6;

    /** 首次所属导购ID-固定 */
    private String followStaffId;

    /** 注册平台 */
    private String regPlatform;

    /** 首次注册渠道一级 */
    private String firstRegChannel1;

    /** 首次注册渠道二级 */
    private String firstRegChannel2;

    /** 首次注册渠道三级 */
    private String firstRegChannel3;

    /** 首次注册渠道四级 */
    private String firstRegChannel4;

    /** 首次注册渠道五级 */
    private String firstRegChannel5;

    /** 首次注册渠道六级 */
    private String firstRegChannel6;

    /** 首次注册时间 */
    private LocalDateTime firstRegTime;

    /** 星妈会注册渠道名称 */
    private String xmhRegChannelName;

    /** 星妈会注册渠道编码 */
    private String xmhRegChannelCode;

    /** 可用积分 */
    private Long usablePoints;

    /** 定级积分 */
    private Long gradingPoints;

    /** unionid */
    private String unionId;

    /** 一胎已做新客订单 */
    private String firstNewOrder;

    /** 一胎已做新客渠道 */
    private String firstCustomerFrom;

    /** 一胎已做新客时间 */
    private String firstNewTime;

    /** 0转婴已做新客订单 */
    private String zeroOrder;

    /** 0转婴已做新客时间 */
    private String zeroTime;

    /** 二胎活动新客渠道 */
    private String secondNewChannel;

    /** 三胎活动新客渠道 */
    private String thirdNewChannel;

    /** 成为新客归属人 */
    private String becomeNewBelong;

    /** 一胎新客归属人 */
    private String firstBabyNewBelong;

    /** 二胎新客归属人 */
    private String secondBabyNewBelong;

    /** 三胎新客归属人 */
    private String thirdBabyNewBelong;

    /** mini_open_id */
    private String miniOpenId;

    /** 新客渠道 */
    private String newChannel;

    /** 是否退货新客 */
    private String isRefundNew;

    /** 新客渠道类型 */
    private String newChannelType;

    public static final String QUERY_SQL = "select  id\n"
            + "       ,member_id\n"
            + "       ,member_name\n"
            + "       ,mobile\n"
            + "       ,birthday\n"
            + "       ,email\n"
            + "       ,gender\n"
            + "       ,member_level\n"
            + "       ,identity\n"
            + "       ,nick_name\n"
            + "       ,wechat\n"
            + "       ,wechat_openid\n"
            + "       ,first_follow_time\n"
            + "       ,is_follow_wechat\n"
            + "       ,follow_wechat_time\n"
            + "       ,unfollow_wechat_time\n"
            + "       ,create_time\n"
            + "       ,member_type\n"
            + "       ,new_time\n"
            + "       ,old_time\n"
            + "       ,logout_time\n"
            + "       ,new_order\n"
            + "       ,old_order\n"
            + "       ,is_logout\n"
            + "       ,first_baby_birthday\n"
            + "       ,second_baby_birthday\n"
            + "       ,third_baby_birthday\n"
            + "       ,minimum_baby_birthday\n"
            + "       ,first_baby_gender\n"
            + "       ,second_baby_gender\n"
            + "       ,third_baby_gender\n"
            + "       ,minimum_baby_gender\n"
            + "       ,member_score\n"
            + "       ,growth_stage\n"
            + "       ,baby_num\n"
            + "       ,baby_avgmonthage\n"
            + "       ,feed_way\n"
            + "       ,reg_channel\n"
            + "       ,belong_vip\n"
            + "       ,refer_mobile\n"
            + "       ,is_canceled\n"
            + "       ,is_black\n"
            + "       ,is_inner_member\n"
            + "       ,is_member\n"
            + "       ,is_delete\n"
            + "       ,follow_channel1\n"
            + "       ,follow_channel2\n"
            + "       ,follow_channel3\n"
            + "       ,follow_channel4\n"
            + "       ,follow_channel5\n"
            + "       ,follow_channel6\n"
            + "       ,new_channel1\n"
            + "       ,new_channel2\n"
            + "       ,new_channel3\n"
            + "       ,new_channel4\n"
            + "       ,old_channel1\n"
            + "       ,old_channel2\n"
            + "       ,old_channel3\n"
            + "       ,old_channel4\n"
            + "       ,xmh_reg_channel1\n"
            + "       ,xmh_reg_channel2\n"
            + "       ,xmh_reg_channel3\n"
            + "       ,xmh_reg_channel4\n"
            + "       ,reg_time\n"
            + "       ,first_baby_name\n"
            + "       ,second_baby_name\n"
            + "       ,third_baby_name\n"
            + "       ,member_level_code\n"
            + "       ,second_customer_from\n"
            + "       ,second_new_order\n"
            + "       ,second_new_time\n"
            + "       ,third_customer_from\n"
            + "       ,third_new_order\n"
            + "       ,third_new_time\n"
            + "       ,last_order\n"
            + "       ,last_time\n"
            + "       ,last_channel1\n"
            + "       ,last_channel2\n"
            + "       ,last_channel3\n"
            + "       ,last_channel4\n"
            + "       ,boy_babys\n"
            + "       ,girl_babys\n"
            + "       ,minimum_baby_month_age\n"
            + "       ,belong_store_code\n"
            + "       ,customized_monthage_time\n"
            + "       ,xmh_reg_channel5\n"
            + "       ,xmh_reg_channel6\n"
            + "       ,follow_staff_id\n"
            + "       ,reg_platform\n"
            + "       ,first_reg_channel1\n"
            + "       ,first_reg_channel2\n"
            + "       ,first_reg_channel3\n"
            + "       ,first_reg_channel4\n"
            + "       ,first_reg_channel5\n"
            + "       ,first_reg_channel6\n"
            + "       ,first_reg_time\n"
            + "       ,xmh_reg_channel_name\n"
            + "       ,xmh_reg_channel_code\n"
            + "       ,usable_points\n"
            + "       ,grading_points\n"
            + "       ,union_id\n"
            + "       ,first_new_order\n"
            + "       ,first_customer_from\n"
            + "       ,first_new_time\n"
            + "       ,zero_order\n"
            + "       ,zero_time\n"
            + "       ,second_new_channel\n"
            + "       ,third_new_channel\n"
            + "       ,become_new_belong\n"
            + "       ,first_baby_new_belong\n"
            + "       ,second_baby_new_belong\n"
            + "       ,third_baby_new_belong\n"
            + "       ,mini_open_id\n"
            + "       ,new_channel\n"
            + "       ,is_refund_new\n"
            + "       ,new_channel_type\n"
            + "from    firmus_dataphin_prd_cdm.dim_customer\n"
            + "where " + PartitionUtil.sqlBizDate()
            + ";";
}
