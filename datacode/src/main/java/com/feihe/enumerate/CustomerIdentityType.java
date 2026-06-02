package com.feihe.enumerate;


import lombok.AllArgsConstructor;
import lombok.Getter;

/**
 * 客户身份类型枚举
 * <p>
 * <a href="https://tmc.qidian.qq.com/base/console/doc/14628?version=20260203">客户元数据查询</a>
 */
@Getter
@AllArgsConstructor
public enum CustomerIdentityType {

    QQ(1, "QQ", "qq"),

    MOBILE(2, "手机号", "mobile"),

    WX_OPENID(3, "微信公众号OpenID", "wx_openid"),

    ANONYMOUS_ID(4, "匿名访客ID", "anonymous_id"),

    WX_APPLET_OPENID(5, "微信小程序OpenID", "wx_applet_openid"),

    EMAIL(7, "邮箱", "email"),

    BUSINESS_ID(10, "业务账号", "business_id"),

    WX_UNIONID(11, "微信unionID", "wx_unionid"),

    EXTERNAL_USER_ID(12, "企业微信外部联系人ID", "external_user_id"),

    IDCARD(13, "身份证", "IDcard"),

    ALI_USERID(15, "支付宝用户ID", "ali_userid"),

    BAIDU_OPENID(16, "百度OpenID", "baidu_openid"),

    BAIDU_UNIONID(17, "百度unionID", "baidu_unionid"),

    ANDROID_ID(18, "安卓设备ID", "android_id"),

    IOS_ID(19, "iOS设备ID", "ios_id"),

    TPNS_TOKEN(20, "移动推送TPNS_token", "TPNS_token"),

    JPUSH_REGID(21, "极光推送RegID", "JPush_regid"),

    BYTE_UNIONID(22, "抖音UnionID", "byte_unionid"),

    BYTE_OPENID(23, "抖音OpenID", "byte_openid"),

    WX_CHANNELS_OPENID(24, "微信小店OpenID", "wx_channels_openid"),

    LM_MEMBER_ID(25, "LM-会员ID", "LM_member_id"),

    GETUI_CLIENTID(26, "个推推送ClientID", "GeTui_clientid"),

    RTC_REGID(27, "RTC推送RegistrationID", "RTC_regid"),

    MEMBER_ID(51, "会员ID", "member_id");


    private final Integer type;
    private final String fieldName;
    private final String fieldKey;
}
