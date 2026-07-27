import type { Metadata } from "next";

import { LegalPage, LegalSection } from "@/components/legal-page";

export const metadata: Metadata = { title: "服务条款", description: "使用开放课堂账号、AI、社区和付费功能的基本约定。" };

export default function TermsPage() {
  return <LegalPage title="服务条款" summary="使用开放课堂即表示你同意遵守以下规则。若你代表学校或组织使用，应确保自己有权代表该组织接受条款。">
    <LegalSection title="1. 账号与资格"><p>用户应提供可验证的账号信息、保护登录凭据，并对账号中的活动负责。不得冒用他人身份、共享受限账号、绕过访问控制或使用自动化方式批量注册。</p></LegalSection>
    <LegalSection title="2. 用户内容"><p>用户保留对上传资料、课程和文档依法享有的权利，并授权开放课堂在提供、保护和改进用户所请求的服务所必需范围内处理这些内容。用户应确保自己有权上传和使用相关内容。</p></LegalSection>
    <LegalSection title="3. AI 输出"><p>AI 输出可能不准确、不完整或不适合特定目的。用户应在教学、考试、医疗、法律、财务或其他重要决策前独立核验。开放课堂不会保证某次模型调用产生特定结果。</p></LegalSection>
    <LegalSection title="4. 可接受使用"><p>不得利用平台侵害他人权利、传播违法内容、实施欺诈或骚扰、生成恶意软件、探测非本人数据、干扰服务稳定性，或规避配额、人机验证、安全控制和第三方服务限制。</p></LegalSection>
    <LegalSection title="5. Credits、付款与退款"><p>页面显示的价格、Credits 数量和有效规则以结账时信息为准。只有支付服务确认成功后才会发放权益。重复扣款、未交付或法律要求的退款可联系客服核查；已实际消耗的模型与服务成本可能影响可退金额。拒付或欺诈调查期间，相关权益可能暂时冻结。</p></LegalSection>
    <LegalSection title="6. 第三方服务"><p>模型、支付、邮件、社区和外部资料可能由第三方提供。第三方服务的可用性、区域、价格和规则可能变化；用户同时受相应第三方条款约束。</p></LegalSection>
    <LegalSection title="7. 服务变更与终止"><p>我们可能为安全、合规、维护或产品调整而限制功能，并尽量减少影响。严重或重复违反条款、危害平台或他人安全时，账号可能被暂停或终止。用户可随时导出数据并注销账号。</p></LegalSection>
    <LegalSection title="8. 责任边界"><p>在适用法律允许范围内，服务按现状提供。开放课堂不排除法律不得排除的责任；对于超出合理控制范围的间接损失或第三方故障，责任按适用法律处理。</p></LegalSection>
    <LegalSection title="9. 争议与适用规则"><p>发生争议时，请先联系我们协商。任何强制性的消费者保护、数据保护和司法管辖规则仍然适用；本条款不减少用户依法享有且不可放弃的权利。</p></LegalSection>
  </LegalPage>;
}
