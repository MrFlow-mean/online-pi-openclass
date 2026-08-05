import type { Metadata } from "next";

import { LegalPage, LegalSection } from "@/components/legal-page";
import { publicContactEmail } from "@/lib/public-site";

export const metadata: Metadata = { title: "Security", description: "OpenClass account security practices and vulnerability reporting." };

export default function SecurityPage() {
  const email = publicContactEmail();
  return <LegalPage title="Safety instructions" summary="We protect accounts, course materials, and model credentials based on the principles of least privilege, session isolation, and revocable access, and continue to improve protection.">
    <LegalSection title="Accounts and Sessions"><p>Formal accounts use HttpOnly security cookies issued by the server to maintain sessions and support password rotation and exit from all sessions. Login, registration, verification code, and password retrieval are subject to frequency limits and Cloudflare Turnstile human verification.</p></LegalSection>
    <LegalSection title="Data and transmission"><p>Production traffic is encrypted via HTTPS; sensitive configurations are not written to the public code repository; user-level model credentials are isolated by account. Public sharing and community posting should not automatically include private material, and users should still review content before posting.</p></LegalSection>
    <LegalSection title="security response"><p>We will record necessary security events, limit abnormal requests, and after confirming the event, take measures such as revoking sessions, fixing vulnerabilities, retaining evidence, and notifying affected users. The specific notification time will be subject to applicable laws and incident investigation needs.</p></LegalSection>
    <LegalSection title="Responsible Disclosure"><p>If you discover a vulnerability that may affect OpenClass or its users, please send an email to <a className="font-semibold text-stone-950 underline" href={`mailto:${email}?subject=OpenClass%20Security%20Report`}>{email}</a>, describing the impact, steps to reproduce and necessary evidence. Do not access other people’s data, disrupt services, conduct social engineering, or expose unpatched vulnerabilities.</p><p>We will acknowledge receipt of the report and assess the risk, but are not committing to bug bounties at this time. Tests that are legitimate, in good faith, and adhere to the above boundaries will be prioritized for coordination.</p></LegalSection>
    <LegalSection title="What users can do"><p>Use a unique and long enough password to protect your email and third-party accounts, and check your login status regularly; if any abnormalities are found, immediately change your password and log out of all sessions. Do not paste passwords, payment credentials, or API keys in courses, chats, or public communities.</p></LegalSection>
  </LegalPage>;
}
