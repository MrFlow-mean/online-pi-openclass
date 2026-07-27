import type { Metadata } from "next";

import { LegalPage, LegalSection } from "@/components/legal-page";
import { publicContactEmail } from "@/lib/public-site";

export const metadata: Metadata = { title: "安全说明", description: "开放课堂的账户安全措施和漏洞报告方式。" };

export default function SecurityPage() {
  const email = publicContactEmail();
  return <LegalPage title="安全说明" summary="我们以最小权限、会话隔离和可撤销访问为原则保护账号、课程资料和模型凭据，并持续改进防护。">
    <LegalSection title="账户与会话"><p>正式账号使用服务器签发的 HttpOnly 安全 Cookie 维持会话，支持密码轮换和退出所有会话。登录、注册、验证码和密码找回会接受频率限制与 Cloudflare Turnstile 人机验证。</p></LegalSection>
    <LegalSection title="数据与传输"><p>生产流量通过 HTTPS 加密；敏感配置不写入公开代码仓库；用户级模型凭据按账号隔离。公开分享和社区发布不应自动包含私有资料，用户仍应在发布前检查内容。</p></LegalSection>
    <LegalSection title="安全响应"><p>我们会记录必要的安全事件、限制异常请求，并在确认事件后采取撤销会话、修复漏洞、保留证据和通知受影响用户等措施。具体通知时间会服从适用法律及事件调查需要。</p></LegalSection>
    <LegalSection title="负责任披露"><p>若发现可能影响开放课堂或其用户的漏洞，请发送邮件至 <a className="font-semibold text-stone-950 underline" href={`mailto:${email}?subject=OpenClass%20Security%20Report`}>{email}</a>，说明影响、复现步骤和必要证据。请勿访问他人数据、破坏服务、进行社会工程或公开尚未修复的漏洞。</p><p>我们会确认收到报告并评估风险，但目前不承诺漏洞奖励。合法、善意且遵守上述边界的测试将被优先协调处理。</p></LegalSection>
    <LegalSection title="用户可以做什么"><p>使用唯一且足够长的密码，保护邮箱和第三方账号，定期检查登录状态；发现异常时立即修改密码并退出所有会话。不要在课程、聊天或公开社区中粘贴密码、支付凭据或 API Key。</p></LegalSection>
  </LegalPage>;
}
