// The one shape of a store: a value, read whole, replaced whole, and a
// list of who wants to know. Every store under state/ is one of these.

export type Store<Value> = {
    get: () => Value;
    set: (value: Value) => void;
    subscribe: (listener: (value: Value) => void) => () => void;
};

export function store<Value>(
    initial: Value,
    equal: (a: Value, b: Value) => boolean = Object.is,
): Store<Value> {
    let value = initial;
    const listeners = new Set<(value: Value) => void>();
    return {
        get: () => value,
        set(next) {
            if (equal(value, next)) {
                return;
            }
            value = next;
            for (const listener of listeners) {
                listener(value);
            }
        },
        subscribe(listener) {
            listeners.add(listener);
            return () => {
                listeners.delete(listener);
            };
        },
    };
}
