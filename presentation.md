# dominion: Automating NIOS from Python

A library for deploying, testing, and managing NIOS appliances programmatically.

---

## Slide 1: The Problem

- Testing NIOS changes today requires manual steps: SSH into appliances, copy files, restart services, navigate the GUI, check results.
- Lab appliances go down. When they do, development stops. There is no local alternative.
- Every manual operation is a one-off. The same steps get repeated by hand, every time, by every engineer.
- Setting up a grid (joining members, configuring HA) takes 30+ minutes of clicking through the GUI.

---

## Slide 2: What dominion Is

dominion is a Python library that gives scripts a structured way to talk to NIOS appliances, both remote (lab VMs) and local (NCC containers on your dev machine).

It provides four entry points:

| Object | What it connects to |
|---|---|
| `Appliance` | A remote lab VM (over SSH) |
| `LocalAppliance` | A local NCC container (docker exec) |
| `NiosContainer` | The NIOS Docker container inside a remote appliance |
| `LocalNiosContainer` | The NIOS Docker container in a local NCC slot |

Each object bundles transport, deploy methods, shell access, and console access into one handle. You create it, and everything is wired up and ready.

---

## Slide 3: Local Appliances — No Lab Required

The biggest capability: `LocalAppliance` and `LocalNiosContainer` let you work against a NIOS instance running on your own machine.

- Uses NCC (NIOS Container Cluster) slots: `balerion`, `drogon`
- No SSH. No network dependency. No lab outages blocking your work.
- The library handles network configuration, WAPI readiness polling, and host routing automatically.
- If the lab goes down tomorrow, development continues.

---

## Slide 4: What You Can Do With a Single Object

Once you have an Appliance or LocalAppliance, you can:

- **Deploy code** — Python files, C binaries, shared libraries, cloud-init modules. Hot-deploy without rebuilding the full image.
- **Open a shell** — Interactive bash inside the NIOS container. If you can reach a shell, you can do anything.
- **Open a console** — The serial console (Infoblox > prompt). Run `set network`, `set membership`, `show status`. Fully automated, no GUI.
- **Run WAPI calls** — REST API access for configuration and verification.
- **Revert changes** — One call restarts the container, discarding all hot-deployed files and restoring the original image.

All of this through Python. All of it scriptable. All of it repeatable.

---

## Slide 5: Deploys That Verify Themselves

Every deploy method follows the same pattern:

1. Hash the local file
2. Upload it to the container
3. Hash the remote copy
4. Compare. If they don't match, raise an error.
5. Restart the affected service

The deploy reports back exactly what changed: which files, whether anything was skipped, whether a service was restarted. No guessing. No "did my file actually get there?"

Supported deploys: token Python (WAPI/IBAP/pyabs), serial console binary, clusterd, shared libraries (.so), cloud-init modules, and the token stats consumer.

---

## Slide 6: Grid Operations — Minutes Instead of an Afternoon

The `Grid` object deploys and wires up multi-member NIOS grids:

- Deploys images to lab VMs
- Parses the resulting topology (GM, members, IPs)
- Returns ready-to-use `Appliance` objects for every node

The HA proof-of-concept (`ha_poc.py`) demonstrates the full sequence locally:
1. Merge Docker network bridges so HA and LAN interfaces share L2
2. Configure network on both nodes via serial console
3. Enable HA via WAPI
4. Join the passive node to the grid via console
5. Verify the pair at the VIP

This is a multi-member HA grid, configured end-to-end, with no GUI. Two NCC containers on a laptop. What used to take an afternoon of manual clicks runs in minutes and produces the same result every time.

---

## Slide 7: Context Engineering for AI

dominion is designed as a context engineering layer for Claude.

**The idea:** instead of giving an AI agent access to everything and hoping it figures out the right approach, you give it a narrow, well-defined set of operations. Appliance objects, deploy methods, session handles. Claude doesn't need to know how SSH transport works or how to pipe files into a Docker container. It calls `appliance.deploy_token_py()` and it works.

This is what context engineering means: constraining the problem space so the AI operates in a lane where it can succeed reliably.

---

## Slide 8: Scripts That Last Forever

The scripts Claude generates using dominion are not throwaway prompts. They are real Python programs that:

- Run independently, no AI needed after creation
- Produce structured output (JUnit XML, summary.txt)
- Can be committed, versioned, and shared
- Work against any appliance, local or remote, by changing one config value

**One conversation with Claude produces a script. That script runs forever.** It doesn't need to be re-prompted. It doesn't drift. The operations are deterministic. You invest minutes to create something that replaces a manual process permanently.

---

## Slide 9: The Test Harness

The `Run` object gives standalone scripts the same structure as pytest, without requiring pytest:

- Named test steps with pass/fail/skip tracking
- Automatic JUnit XML generation (integrates with CI reporting)
- Timestamped output directories with summary files
- Clean exit codes (0 = all passed, 1 = something failed)

Engineers write ticket tests as standalone scripts. Each script creates a `Run`, calls `run.test("step name", fn)` for each step, and `run.summary(appliance)` at the end. Results go to a standard directory. No framework lock-in.

---

## Slide 10: The Value

| Before | After |
|---|---|
| Lab goes down, work stops | Local appliances, work continues |
| Manual deploys, hope the file landed | Verified deploys with hash checks |
| GUI clicks to join a grid | Python script, runs in minutes |
| Repeat the same steps every time | Write once, run forever |
| AI generates fragile one-off commands | AI generates durable, testable scripts |
| Each engineer reinvents the wheel | Shared library, consistent patterns |

dominion turns manual, error-prone, one-off operations into scriptable, repeatable, self-verifying automation. It makes AI-assisted development reliable by giving Claude a structured interface instead of raw shell access. And it eliminates lab dependency for the most common development workflows.
