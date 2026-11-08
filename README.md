# Digital Payment Wallet

A prototype digital payment wallet developed as part of the **AI in Software Engineering** case study (Case 23).

The project demonstrates the software development lifecycle, Agile practices, AI-assisted development, automated testing, and software quality assurance through a simplified digital wallet system.

## Project Overview

The wallet prototype focuses on three core operations:

- **Wallet Top-up:** Add funds to a user's wallet.
- **Wallet Transfer:** Transfer funds between two wallet accounts.
- **Merchant Payment:** Make payments using the available wallet balance.

A key requirement is ensuring that a failed or repeated transfer request never results in the same transaction being debited twice.

## Key Requirements

| ID | Requirement | Description |
|---|---|---|
| FR-01 | Wallet Transfer | Transfer funds between two wallet accounts. |
| FR-02 | Wallet Top-up | Add funds to a wallet. |
| FR-03 | Merchant Payment | Pay merchants using wallet funds. |
| FR-04 | Idempotency | Prevent duplicate transactions when a request is retried. |
| NFR-01 | Reliability | Maintain consistent wallet balances during transactions. |
| NFR-02 | Security | Protect transaction data and sensitive credentials. |

## Technology Stack

- **Language:** Python
- **Version Control:** Git and GitHub
- **Testing:** pytest
- **AI-assisted Development:** GitHub Copilot and Tabnine
- **CI/CD:** Jenkins
- **Containerization:** Docker
- **Code Quality:** SonarQube
- **UI Testing:** Selenium

## Project Structure

```text
Digital-Payment-Wallet/
│
├── src/
│   ├── __init__.py
│   └── wallet.py
│
├── tests/
│   └── test_wallet.py
│
├── .gitignore
└── README.md
```

## Getting Started

### 1. Clone the repository

```bash
git clone <YOUR_REPOSITORY_URL>
cd Digital-Payment-Wallet
```

### 2. Create a virtual environment

```bash
python3 -m venv venv
source venv/bin/activate
```

### 3. Install dependencies

```bash
pip install pytest
```

### 4. Run the tests

```bash
pytest -v
```

## Important Design Consideration: Idempotent Transfers

The wallet transfer operation must be idempotent.

Each transfer request should have a unique idempotency key. If the same request is submitted multiple times due to network failures or retries, the system should recognise the previously processed request instead of executing the transfer again.

This helps prevent duplicate debits and maintains transaction consistency.

## Development Approach

The project follows the **V-Model** for software development and uses **Scrum** for Agile project management.

AI-assisted development tools are used to support code generation, requirement analysis, test generation, and code review. All AI-generated suggestions must be reviewed and tested before being accepted.

## Project Status

This project is being developed as an academic prototype. It is intended for demonstrating software engineering practices and is not designed for processing real financial transactions.

---

**Case Study:** 23 – Digital Payment Wallet  
**Subject:** AI in Software Engineering
