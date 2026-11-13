# AI Usage Disclosure

## Tools used
ChatGPT (OpenAI).

## What I used them for
I used ChatGPT as a learning aid while completing Assignment B. It explained unfamiliar parts of the assignment and concepts such as scheduling windows, SQLite persistence, idempotency, provider APIs, signed webhooks, retries, callbacks, and crash recovery. It also suggested code snippets and reviewed the code as I entered and integrated it in the project.

## What I wrote or decided myself
I chose Assignment B and drove the learning process by asking for explanations, working through the implementation, and asking for changes when something was unclear. I entered and integrated the code in my project and am responsible for understanding and being able to explain the decisions and code I submit.

## Where the AI was wrong or unhelpful
At first, ChatGPT moved too quickly and provided code before explaining it. During review, we found and corrected a duplicated, incorrectly indented section in the SQLite store. We also found a scheduling helper that rounded the current time down to a minute; that was corrected and covered by a new test.

## How I verified AI-generated code
I manually checked the calling-window helper at the opening and closing times. The automated suite passes with 10 tests. I also ran the dispatcher against the mock provider with failures disabled and enabled, force-stopped and restarted it during an active call, and compared both 200-row output files field-by-field with `/admin/leads`. Both runs had zero differences and the provider reported zero rule violations.
