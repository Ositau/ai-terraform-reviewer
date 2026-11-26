# AI Terraform plan reviewer

Nobody reads a 400-line `terraform plan` properly. This tool reads it for you on every pull request: it explains in plain English what will change, flags anything risky, asks the author the questions a careful reviewer would, and stops the merge when the risk is high until someone with write access accepts it.

It runs in **GitHub Actions** or **Azure DevOps**, signs in to **Azure OpenAI** with workload identity (no API keys), and costs well under a penny per review.

## How it decides

1. **Fixed safety checks run first.** Deleting or replacing anything that holds data, opening ports to the internet, public storage, old TLS, broad roles such as Owner, open database firewalls. These can't be talked out of a finding.
2. **Azure OpenAI reviews the plan** (gpt-4o-mini, temperature 0, a fixed JSON answer format). It explains each change and adds concerns no rule covers: a VM size that multiplies the bill, HTTPS switched off, password login turned on, a Key Vault opened to all networks.
3. **The final risk is the higher of the two.** The AI can raise the risk, never lower it. If the AI is unavailable, the safety checks still run and the comment says so.

Secrets never reach the model: anything Terraform marks as sensitive is replaced with `(sensitive)` before the prompt is built.

## Example comment

> ## Terraform plan review: **HIGH** risk
> **0 to create, 1 to update, 0 to replace, 0 to delete.**
>
> The planned changes will allow password authentication for the virtual machine, which could weaken security...
>
> | Severity | Resource | Why |
> |---|---|---|
> | **HIGH** | `azurerm_linux_virtual_machine.api` | Enabling password authentication can expose the VM to brute-force attacks. _(AI review)_ |
>
> **This change needs explicit approval before it is applied.**

## The reviewer is tested too

`eval/` holds 17 plans: routine changes, obvious dangers, dangers no fixed rule covers, and one real plan captured from a project (IDs masked). `eval/run_eval.py` runs them all through the real model and fails if any result is outside the expected range or misses a resource it must flag. CI runs it whenever the prompt, rules or cases change, so a "small prompt tweak" can't quietly make the reviewer worse.

Latest run (gpt-4o-mini):

| | Result |
|---|---|
| Cases passed | 17/17 |
| Dangers no fixed rule covers, caught by the AI | 6/6 |
| Tokens for all 17 reviews | about 12,000 (well under 1p) |
| Time per review | about 4 seconds |

## Live demo

[Ositau/azure-openai-policy-assistant#1](https://github.com/Ositau/azure-openai-policy-assistant/pull/1) looks like a tidy-up: "upgrade AI Search to Basic and enable API keys for testing". The reviewer rated it **HIGH**: changing the search tier destroys and recreates the service (and its index), six role assignments are recreated with it, the new tier costs more, and API keys are switched on for Azure OpenAI. The check failed until someone accepts the risk. The pull request was never merged.

The demo workflow plans with a read-only identity: Reader on the resource group, Storage Blob Data Reader on the Terraform state and Cognitive Services OpenAI User. So it runs `terraform plan -refresh=false -lock=false`: no key access, and nothing written to the state.

## Use it

### Locally

```bash
pip install -r requirements.txt
terraform plan -out tfplan && terraform show -json tfplan > plan.json
az login
export AZURE_OPENAI_ENDPOINT=https://<your-openai>.openai.azure.com/
python -m reviewer plan.json --out review.md
```

Exit code `2` means the risk reached `--fail-on` (default `high`); `--no-ai` runs the safety checks only.

### GitHub Actions

```yaml
permissions:
  id-token: write
  contents: read
  pull-requests: write
steps:
  - uses: azure/login@v2
    with: { client-id: ..., tenant-id: ..., subscription-id: ... }
  - run: terraform plan -out tfplan && terraform show -json tfplan > plan.json
  - uses: Ositau/ai-terraform-reviewer@main
    with:
      plan-json: plan.json
      azure-openai-endpoint: ${{ vars.AZURE_OPENAI_ENDPOINT }}
```

A high-risk plan fails the check. After reading the comment, someone with write access adds the `risk-accepted` label to let it through.

### Azure DevOps

Use the step template in `azure-pipelines/review-plan.yml` after your plan step. It runs inside `AzureCLI@2` with your workload identity service connection and posts the review on Azure Repos pull requests.

The identity that runs the review needs the **Cognitive Services OpenAI User** role on the Azure OpenAI resource. Nothing else.

## Project layout

| Path | What it does |
|---|---|
| `reviewer/plan.py` | Reads the plan JSON, keeps only what changes, removes secrets |
| `reviewer/rules.py` | The fixed safety checks |
| `reviewer/ai.py` | The Azure OpenAI review (prompt and JSON schema) |
| `reviewer/report.py` | Combines both and writes the pull request comment |
| `eval/` | The 17 test plans, expected results and the evaluation script |
| `tests/` | Unit tests (no Azure needed) |
| `action.yml` | The GitHub Action |
| `azure-pipelines/` | The Azure DevOps step template and comment poster |

Written by [Osita Uche](https://ositauche.com).
