import type { Metadata } from "next";

import { LegalPage, LegalSection } from "@/components/legal-page";

export const metadata: Metadata = { title: "隐私政策", description: "开放课堂如何收集、使用、保存和保护个人信息。" };

export default function PrivacyPage() {
  return <LegalPage title="隐私政策" summary="本政策说明开放课堂在提供 AI 课程工作台、资料处理、社区、账号和支付能力时如何处理个人信息。">
    <LegalSection title="1. 我们处理的信息"><p>我们可能处理账号资料（邮箱、用户名、登录身份）、课程与文档内容、用户上传的资料、操作与安全日志、设备和网络信息，以及购买 Credits 时由支付服务返回的订单状态。我们不会保存完整银行卡号。</p><p>用户主动连接第三方模型或登录服务时，我们只处理完成该连接所需的信息。用户提供的 API Key 会放在按账户隔离且访问权限受限的服务端存储中，不会展示给其他用户。</p></LegalSection>
    <LegalSection title="2. 使用目的"><p>这些信息用于创建和保护账号、同步工作台、解析资料、生成或编辑学习内容、履行付款与退款、预防滥用、诊断故障、发送用户请求的验证码和安全通知，以及履行法律义务。</p></LegalSection>
    <LegalSection title="3. AI 与第三方服务"><p>当用户选择某个 AI 服务、支付服务、邮件服务或社区功能时，完成请求所必需的输入会发送给相应服务商，并受其条款和隐私政策约束。开放课堂不会为了广告出售个人信息，也不会将私有课程默认公开。</p></LegalSection>
    <LegalSection title="4. 保存与删除"><p>账号与课程数据通常保存至用户删除相关内容或注销账号；安全、账务和合规记录会在履行法定义务、处理争议和防止欺诈所需期限内保留。备份中的删除会随备份轮换完成。</p></LegalSection>
    <LegalSection title="5. 用户权利"><p>用户可在账户设置中修改密码、退出所有会话、导出数据或申请注销账号，也可联系我们请求访问、更正、删除或限制处理。为保护账号，我们可能先验证请求者身份。</p></LegalSection>
    <LegalSection title="6. Cookie 与安全"><p>我们使用必要 Cookie 维持登录和防止跨站请求伪造，并可使用 Cloudflare Turnstile 判断请求是否来自自动化程序。我们采用访问控制、加密传输、会话撤销和审计措施，但任何网络服务都无法承诺绝对安全。</p></LegalSection>
    <LegalSection title="7. 未成年人"><p>未达到所在地独立同意年龄的用户，应在监护人或学校授权下使用。若发现未经适当授权收集了未成年人个人信息，我们会在核实后处理删除请求。</p></LegalSection>
    <LegalSection title="8. 变更"><p>政策发生重要变化时，我们会更新日期，并通过产品内通知或适当联系方式告知。继续使用前，用户可以查看新版本并决定是否接受。</p></LegalSection>
  </LegalPage>;
}
