"""HTTP 请求/响应模型（从 main.py 抽出，#16）。

必须模块级定义，否则 FastAPI 在 `from __future__ import annotations` 下解析不到。
"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class LoginReq(BaseModel):
    username: str
    password: str


class ChatRequest(BaseModel):
    question: str
    conversation_id: Optional[str] = None
    force_refresh: bool = False
    skip_llm_narrative: bool = False
    # 右上角下拉框选的模型，None=用 env 默认（线上=feihe）
    llm_provider: Optional[str] = None


class ConversationCreateReq(BaseModel):
    title: str = "新会话"


class ConversationRenameReq(BaseModel):
    title: str = "新会话"


class FeishuPushReq(BaseModel):
    """安全（P0）：推送的经营结论（narrative/highlights/数据预览）必须由后端按 trace
    从会话存储取可信结果生成，不接受前端传入，杜绝伪造结论推送。
    前端仅可传：定位用的 conversation_id / trace_id，以及（仅 admin 生效的）收件邮箱。"""
    conversation_id: str
    trace_id: str
    user_email: Optional[str] = None     # 仅 admin 生效；普通用户忽略（用绑定邮箱）


class ReportRequest(BaseModel):
    """安全（P0）：报告内容必须由后端按 trace 从会话存储取可信结果生成，
    不接受前端传入的 question/answer/plan/sql，杜绝用伪造 payload 生成报告。"""
    conversation_id: str
    trace_id: str
    template_id: Optional[str] = None    # 留空 = 用默认模板


class ReportTemplateReq(BaseModel):
    name: str
    prompt: str
    is_default: bool = False
    system: bool = False   # 仅 admin 生效：true 时创建系统级模板(user_id="")，否则私有模板


class ReportTemplatePatchReq(BaseModel):
    name: Optional[str] = None
    prompt: Optional[str] = None
    is_default: Optional[bool] = None


class LLMSettingsPutReq(BaseModel):
    """[legacy] 管理页旧的"单条配置"接口入参（None=不动；""=清除）。新前端走 preset CRUD。"""
    DASHSCOPE_API_KEY: Optional[str] = Field(default=None, description="百炼 AK，sk-...")
    DASHSCOPE_BASE_URL: Optional[str] = Field(default=None, description="百炼 base URL")
    DASHSCOPE_MODEL: Optional[str] = Field(default=None, description="百炼 chat 模型名 (qwen-plus / qwen-max / qwen3.6-max-preview 等)")
    DASHSCOPE_EMBED_MODEL: Optional[str] = Field(default=None, description="百炼 embedding 模型 (text-embedding-v3 等)")
    LLM_PROVIDER: Optional[str] = Field(default=None, description="默认 provider: bailian / feihe")


class LLMPresetCreateReq(BaseModel):
    name: str = Field(..., description="显示名，唯一")
    provider: str = Field(..., description="'bailian' 或 'feihe'")
    api_key: str = Field("", description="bailian 必填；feihe 留空（AES_KEY 在服务器 .env）")
    base_url: str = Field("", description="bailian: https://dashscope.aliyuncs.com/compatible-mode/v1")
    model: str = Field(..., description="chat 模型名（如 qwen-plus / qwen-max）")
    embed_model: str = Field("", description="bailian 才用，如 text-embedding-v3")


class LLMPresetPatchReq(BaseModel):
    name: Optional[str] = None
    provider: Optional[str] = None
    api_key: Optional[str] = None   # None=不动；""=清空；非空=替换
    base_url: Optional[str] = None
    model: Optional[str] = None
    embed_model: Optional[str] = None
    is_active: Optional[bool] = None


class LLMPresetTestReq(BaseModel):
    """保存前测试：用候选配置直发一次 chat，不写库。

    编辑场景（P1）：未输入新 AK 但改了 base_url/model 时，前端传 preset_id + 空 api_key，
    后端用该 preset 已存的旧 AK + 本次草稿的 base_url/model 合并测试（测的就是即将保存的配置）。"""
    provider: str
    api_key: str = ""
    base_url: str = ""
    model: str
    prompt: Optional[str] = None
    preset_id: Optional[str] = None    # 仅"旧 AK + 草稿字段"合并测试时用


class FolderCreateReq(BaseModel):
    name: str
    color: str = ""


class FolderRenameReq(BaseModel):
    name: str
    color: Optional[str] = None


class CollectionReq(BaseModel):
    conversation_id: str
    folder_id: str


class CreateUserReq(BaseModel):
    username: str
    password: Optional[str] = None       # 留空则后端随机生成一次性强密码
    role: str = "user"
    email: str = ""                       # 用户的飞书邮箱（飞书推送用）
    must_change_password: bool = True     # 后台创建的用户默认强制改密


class ResetPasswordReq(BaseModel):
    new_password: Optional[str] = None    # 留空 = 随机生成一次性密码并返回
    must_change_password: bool = True


class UserActiveReq(BaseModel):
    is_active: bool                       # true=启用；false=停用（禁止登录 + 旧 token 失效）


class MyPasswordReq(BaseModel):
    old_password: str
    new_password: str


class MyProfileReq(BaseModel):
    email: Optional[str] = None


class SemanticPutReq(BaseModel):
    content: str           # 完整 YAML 文本


class SemanticEntityReq(BaseModel):
    name: str
    body: dict[str, Any] = Field(default_factory=dict)


class SemanticAnalyzeReq(BaseModel):
    table: str             # 物理表名（chatbi 库中实际存在的表）
    sample_rows: int = 5


class SemanticStatusReq(BaseModel):
    status: str            # draft | verified


class ChatFeedbackReq(BaseModel):
    """问数答案反馈：up=采纳（沉淀为 few-shot），down=点踩（进 bad case 库）。"""
    conversation_id: str
    trace_id: str
    vote: str = "up"       # up | down


class PermissionsPutReq(BaseModel):
    """完整权限配置 — 任一字段省略 = 不变；明确传 {} 或 [] = 清空。"""
    row_rules:        Optional[dict[str, list[str]]] = None
    allowed_tables:   Optional[list[str]] = None
    allowed_columns:  Optional[dict[str, list[str]]] = None
    deny_by_default:  Optional[bool] = None
