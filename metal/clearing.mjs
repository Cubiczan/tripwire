// Kit 12: pay an x402 URL by Metal clearing (bajillion) on TESTNET. Usage: node clearing.mjs deposit <usdz> | pay <url> | balance
import { readFileSync } from "node:fs";
import { privateKeyToAccount } from "viem/accounts";
import { createMetalPaymentClient, createClearingClient, registerClearingClient, depositToClearing, clearingSpendable, USDZ, usdzBaseUnits } from "@metalntwx/metal-pay";
import { openClearingWallet } from "@metalntwx/metal-pay/node";
const KEYS = process.env.METAL_KEY_DIR || "../.metal-keys";
const rpcUrl = process.env.METAL_RPC_URL; if (!rpcUrl) { console.log(JSON.stringify({ ok: false, error: "METAL_RPC_URL missing" })); process.exit(2); }
const sup = await (await fetch("https://x402-testnet.metalntwx.com/x402/facilitator/supported")).json();
const ex = sup.kinds.find(k => k.scheme === "bajillion" && k.network === "eip155:63825").extra;
const account = privateKeyToAccount(JSON.parse(readFileSync(`${KEYS}/agent.json`, "utf8")).privateKey);
const wallet = await openClearingWallet(`${KEYS}/clearing-wallet`);
const [cmd, arg] = process.argv.slice(2);
try {
  if (cmd === "deposit") {
    const r = await depositToClearing({ rpcUrl, chainId: 63825, from: account, settlement: ex.settlement, asset: USDZ.address, account: wallet.signer.publicKey, amount: BigInt(usdzBaseUnits(arg || "0.1")) });
    console.log(JSON.stringify({ ok: true, deposit: arg || "0.1", result: JSON.parse(JSON.stringify(r, (k, v) => typeof v === "bigint" ? v.toString() : v)) }));
  } else if (cmd === "balance") {
    const s = await clearingSpendable({ rpcUrl, settlement: ex.settlement, serviceUrl: ex.serviceUrl, account: wallet.signer.publicKey });
    console.log(JSON.stringify({ ok: true, spendable_base_units: s.toString() }));
  } else {
    const { client, fetchWithPayment } = createMetalPaymentClient({ account, network: "testnet" });
    registerClearingClient(client, "eip155:63825", createClearingClient({ serviceUrl: ex.serviceUrl, rpcUrl, network: "eip155:63825", settlement: ex.settlement, asset: USDZ.address, signer: wallet.signer, store: wallet.store, maxAmount: BigInt(usdzBaseUnits("0.05")) }));
    let r, tries = 0;
    for (;;) { try { r = await fetchWithPayment(arg); break; } catch (e) { const rs = e?.cause?.reason; if ((rs === "between_epochs" || rs === "payment_held" || rs === "answer_lost") && ++tries < 40) { await new Promise(z => setTimeout(z, e?.cause?.retry?.ms || 3000)); continue; } throw e; } }
    const body = await r.text();
    const pr = r.headers.get("payment-response"); let settle = null; try { settle = JSON.parse(Buffer.from(pr, "base64").toString()); } catch {}
    console.log(JSON.stringify({ ok: r.ok, status: r.status, scheme: "bajillion", receipt_status: settle?.extra?.clearing?.status ?? null, settlement_id: settle?.extra?.settlement?.id ?? null, body: body.slice(0, 200) }));
  }
} catch (e) { console.log(JSON.stringify({ ok: false, error: String(e?.cause?.message || e?.message || e).slice(0, 300), reason: e?.cause?.reason })); process.exitCode = 1; }
finally { await wallet.close?.(); }
