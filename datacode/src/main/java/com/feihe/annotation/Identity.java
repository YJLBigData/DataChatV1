package com.feihe.annotation;

import com.feihe.enumerate.CustomerIdentityType;

import java.lang.annotation.ElementType;
import java.lang.annotation.Retention;
import java.lang.annotation.RetentionPolicy;
import java.lang.annotation.Target;

/**
 * 表示当前用户表中的某个属性是客户身份属性
 * <p>
 * 如下表示当前实体的memberId属性是客户身份属性
 * <pre>{@code
 * public class {
 *       @Identity(CustomerIdentityType.MEMBER_ID)
 *       private String memberId;
 *       private String name;
 * }<pre/>
 */
@Target({ElementType.FIELD})
@Retention(RetentionPolicy.RUNTIME)
public @interface Identity {
    CustomerIdentityType value();
}
