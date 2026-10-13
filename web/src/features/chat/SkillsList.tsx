import { Fragment } from "react";
import { bootstrap } from "@/lib/bootstrap";
import { cx } from "@/lib/cx";
import { Icon } from "@/lib/icons";
import { setSkill } from "./controller";
import { COL } from "./layout";
import { pipeWords } from "./rules";
import { useChat } from "./run";

/**
 * What to try, on the empty sheet. Six rows, not six cards: a name, what it
 * does, and the tools it runs through. Reads as a list because it is one.
 * Picking a row arms the skill for the next message.
 */
export function SkillsList() {
  const empty = useChat((s) => s.view === "empty");
  const skills = bootstrap().skills;
  // The heading goes with the list it introduces.
  if (!skills.length) return null;

  return (
    <section
      id="skills"
      aria-labelledby="skills-label"
      hidden={!empty}
      className={cx(COL, "shrink-0 py-6")}
    >
      <h2
        id="skills-label"
        className="arrive mb-2 text-2 font-[400] text-ink-2"
        style={{ "--i": 3 } as React.CSSProperties}
      >
        Pick a skill, or just type.
      </h2>
      <ul id="skill-grid" className="list-none m-0 p-0 border-t border-rule">
        {skills.map((skill, n) => (
          <li
            key={skill.id}
            className="arrive border-b border-rule"
            // The first six follow the heading one after another.
            style={{ "--i": n < 6 ? 3.5 + n * 0.5 : 0 } as React.CSSProperties}
          >
            <button
              type="button"
              data-skill={skill.id}
              onClick={() => setSkill(skill)}
              className={cx(
                "group/skill grid grid-cols-[11rem_minmax(0,1fr)_auto] items-baseline gap-x-4",
                "w-full py-3 px-0 border-0 bg-transparent text-ink text-2 text-start cursor-pointer",
                // The hover tint runs past the text on both sides, so the row
                // reads as one target rather than a highlight hugging the words.
                "[box-shadow:0_0_0_0_var(--sheet-2)]",
                "transition-[background,box-shadow] duration-(--dur-1)",
                "hover:bg-sheet-2",
                "hover:[box-shadow:calc(var(--s-3)*-1)_0_0_0_var(--sheet-2),var(--s-3)_0_0_0_var(--sheet-2)]",
                "le-620:grid-cols-[minmax(0,1fr)] le-620:gap-y-[2px]",
                "coarse:min-h-[56px]",
              )}
            >
              <span className="font-semibold">{skill.name}</span>
              <span className="text-ink-2">{skill.blurb}</span>
              {/* A skill row answers the pointer: its route leans the way it runs. */}
              <span
                className={cx(
                  "inline-flex items-center gap-[6px] text-ink-2 text-1 whitespace-nowrap",
                  "[&_.icon]:transition-[translate] [&_.icon]:duration-(--dur-2)",
                  "[&_.icon]:ease-arrive motion-reduce:[&_.icon]:transition-none",
                  "group-hover/skill:[&_.icon]:translate-x-[3px]",
                )}
              >
                {pipeWords(skill.pipeline).map((word, i) => (
                  // biome-ignore lint/suspicious/noArrayIndexKey: a pipeline is an ordered list and may name a tool twice
                  <Fragment key={i}>
                    {i > 0 && <Icon name="arrow" size={12} />}
                    {word}
                  </Fragment>
                ))}
              </span>
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}
