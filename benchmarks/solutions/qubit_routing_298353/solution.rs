// EVOLVE-BLOCK-START
// Preserve the incumbent bounded two-ply LightSABRE swap policy, while making
// its dense initial subset circuit-aware.  A deterministic, early-weighted
// interaction embedding competes with the original positional zip on exactly
// the same physical subset; bounded pair exchanges refine it, and a 1.5% guard
// keeps the incumbent unless the embedding's own distance objective (including
// a modest first-layer adjacency bonus) is materially better.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SetScaling {
    Constant,
    Size,
}

#[derive(Debug, Clone, Default)]
struct FrontLayerScores {
    nodes: Vec<[usize; 2]>,
    qubits: Vec<Option<(usize, usize)>>,
}

impl FrontLayerScores {
    fn from_ctx(ctx: &SwapSelectionContext<'_>) -> Self {
        let mut out = Self {
            nodes: Vec::new(),
            qubits: vec![None; ctx.topology().num_qubits()],
        };
        for pair in ctx.front_layer().physical_pairs() {
            let [a, b] = *pair;
            let index = out.nodes.len();
            out.nodes.push([a, b]);
            out.qubits[a] = Some((index, b));
            out.qubits[b] = Some((index, a));
        }
        out
    }

    fn len(&self) -> usize {
        self.nodes.len()
    }

    fn is_empty(&self) -> bool {
        self.nodes.is_empty()
    }

    fn is_active(&self, qubit: usize) -> bool {
        self.qubits[qubit].is_some()
    }

    fn iter_active(&self) -> impl Iterator<Item = &usize> {
        self.nodes.iter().flatten()
    }

    fn total_score(&self, topology: TopologyView<'_>) -> f64 {
        self.nodes
            .iter()
            .map(|pair| topology.distance(pair[0], pair[1]) as f64)
            .sum()
    }

    fn score_delta(&self, swap: (usize, usize), topology: TopologyView<'_>) -> f64 {
        let (a, b) = swap;
        let mut delta = 0.0;
        if let Some((_, c)) = self.qubits[a] {
            delta += (topology.distance(b, c) as f64) - (topology.distance(a, c) as f64);
        }
        if let Some((_, c)) = self.qubits[b] {
            delta += (topology.distance(a, c) as f64) - (topology.distance(b, c) as f64);
        }
        delta
    }

    fn after_swap(&self, swap: (usize, usize)) -> Self {
        let mut out = Self {
            nodes: Vec::with_capacity(self.nodes.len()),
            qubits: vec![None; self.qubits.len()],
        };
        for pair in &self.nodes {
            let mapped = [
                moved_by_swap(pair[0], swap),
                moved_by_swap(pair[1], swap),
            ];
            let index = out.nodes.len();
            out.nodes.push(mapped);
            out.qubits[mapped[0]] = Some((index, mapped[1]));
            out.qubits[mapped[1]] = Some((index, mapped[0]));
        }
        out
    }

    fn objective(&self, topology: TopologyView<'_>, weight: f64, reward: f64) -> f64 {
        self.nodes
            .iter()
            .map(|pair| {
                let distance = topology.distance(pair[0], pair[1]);
                weight * ((distance as f64) - if distance == 1 { reward } else { 0.0 })
            })
            .sum()
    }

    fn objective_delta(
        &self,
        swap: (usize, usize),
        topology: TopologyView<'_>,
        weight: f64,
        reward: f64,
    ) -> f64 {
        let mut indices = Vec::<usize>::with_capacity(2);
        if let Some((index, _)) = self.qubits[swap.0] {
            indices.push(index);
        }
        if let Some((index, _)) = self.qubits[swap.1] {
            if !indices.contains(&index) {
                indices.push(index);
            }
        }

        let mut delta = 0.0;
        for index in indices {
            let pair = self.nodes[index];
            let old_distance = topology.distance(pair[0], pair[1]);
            let new_distance = topology.distance(
                moved_by_swap(pair[0], swap),
                moved_by_swap(pair[1], swap),
            );
            let old_cost =
                weight * ((old_distance as f64) - if old_distance == 1 { reward } else { 0.0 });
            let new_cost =
                weight * ((new_distance as f64) - if new_distance == 1 { reward } else { 0.0 });
            delta += new_cost - old_cost;
        }
        delta
    }
}

#[derive(Debug, Clone)]
struct ExtendedSetScores {
    qubits: Vec<Vec<usize>>,
    pairs: Vec<[usize; 2]>,
    len: usize,
}

impl ExtendedSetScores {
    fn new(num_qubits: usize) -> Self {
        Self {
            qubits: vec![Vec::new(); num_qubits],
            pairs: Vec::new(),
            len: 0,
        }
    }

    fn push(&mut self, a: usize, b: usize) {
        self.qubits[a].push(b);
        self.qubits[b].push(a);
        self.pairs.push([a, b]);
        self.len += 1;
    }

    fn len(&self) -> usize {
        self.len
    }

    fn is_empty(&self) -> bool {
        self.len == 0
    }

    fn total_score(&self, topology: TopologyView<'_>) -> f64 {
        self.qubits
            .iter()
            .enumerate()
            .flat_map(|(a, others)| {
                others
                    .iter()
                    .map(move |b| topology.distance(a, *b) as f64)
            })
            .sum::<f64>()
            * 0.5
    }

    fn score_delta(&self, swap: (usize, usize), topology: TopologyView<'_>) -> f64 {
        let (a, b) = swap;
        let mut total = 0.0;
        for other in &self.qubits[a] {
            if *other == b {
                continue;
            }
            total += (topology.distance(b, *other) as f64) - (topology.distance(a, *other) as f64);
        }
        for other in &self.qubits[b] {
            if *other == a {
                continue;
            }
            total += (topology.distance(a, *other) as f64) - (topology.distance(b, *other) as f64);
        }
        total
    }

    fn after_swap(&self, swap: (usize, usize)) -> Self {
        let mut out = Self::new(self.qubits.len());
        for pair in &self.pairs {
            out.push(
                moved_by_swap(pair[0], swap),
                moved_by_swap(pair[1], swap),
            );
        }
        out
    }

    fn objective(&self, topology: TopologyView<'_>, weight: f64, reward: f64) -> f64 {
        self.pairs
            .iter()
            .map(|pair| {
                let distance = topology.distance(pair[0], pair[1]);
                weight * ((distance as f64) - if distance == 1 { reward } else { 0.0 })
            })
            .sum()
    }

    fn objective_delta(
        &self,
        swap: (usize, usize),
        topology: TopologyView<'_>,
        weight: f64,
        reward: f64,
    ) -> f64 {
        let mut delta = 0.0;
        for pair in &self.pairs {
            if pair[0] != swap.0
                && pair[0] != swap.1
                && pair[1] != swap.0
                && pair[1] != swap.1
            {
                continue;
            }
            let old_distance = topology.distance(pair[0], pair[1]);
            let new_distance = topology.distance(
                moved_by_swap(pair[0], swap),
                moved_by_swap(pair[1], swap),
            );
            let old_cost =
                weight * ((old_distance as f64) - if old_distance == 1 { reward } else { 0.0 });
            let new_cost =
                weight * ((new_distance as f64) - if new_distance == 1 { reward } else { 0.0 });
            delta += new_cost - old_cost;
        }
        delta
    }
}

fn build_extended_set(ctx: &SwapSelectionContext<'_>, max_size: usize) -> ExtendedSetScores {
    let mut out = ExtendedSetScores::new(ctx.topology().num_qubits());
    if max_size == 0 {
        return out;
    }

    let precomputed = ctx.precomputed_extended_set_logical_pairs();
    if !precomputed.is_empty() {
        for pair in precomputed.iter().take(max_size) {
            out.push(
                ctx.layout().physical_of_logical(pair[0]),
                ctx.layout().physical_of_logical(pair[1]),
            );
        }
        return out;
    }

    let mut required_predecessors = ctx.remaining().remaining_predecessor_counts().to_vec();
    let mut to_visit = ctx.front_layer().node_ids().to_vec();
    let mut decremented = Vec::<(usize, usize)>::new();
    let mut i = 0usize;
    while i < to_visit.len() && out.len() < max_size {
        let node_id = to_visit[i];
        for &successor in ctx.circuit().node(node_id).successors() {
            if let Some((_, amount)) = decremented.iter_mut().find(|(idx, _)| *idx == successor) {
                *amount += 1;
            } else {
                decremented.push((successor, 1));
            }
            required_predecessors[successor] -= 1;
            if required_predecessors[successor] == 0 {
                if let Some((a, b)) = ctx.circuit().node(successor).two_qubit_pair() {
                    out.push(ctx.layout().physical_of_logical(a), ctx.layout().physical_of_logical(b));
                }
                to_visit.push(successor);
            }
        }
        i += 1;
    }

    out
}

fn enumerate_candidate_swaps(
    topology: TopologyView<'_>,
    front_layer: &FrontLayerScores,
) -> Vec<(usize, usize)> {
    let mut out = Vec::<(usize, usize)>::new();

    for &phys in front_layer.iter_active() {
        for &neighbor in topology.neighbors(phys) {
            if neighbor > phys || !front_layer.is_active(neighbor) {
                out.push((phys, neighbor));
            }
        }
    }

    out
}

fn moved_by_swap(physical: usize, swap: (usize, usize)) -> usize {
    if physical == swap.0 {
        swap.1
    } else if physical == swap.1 {
        swap.0
    } else {
        physical
    }
}

fn virtual_position(
    physical: usize,
    first: (usize, usize),
    second: Option<(usize, usize)>,
) -> usize {
    let after_first = moved_by_swap(physical, first);
    match second {
        Some(swap) => moved_by_swap(after_first, swap),
        None => after_first,
    }
}

fn virtual_pair_distance(
    topology: TopologyView<'_>,
    pair: [usize; 2],
    first: (usize, usize),
    second: Option<(usize, usize)>,
) -> u32 {
    topology.distance(
        virtual_position(pair[0], first, second),
        virtual_position(pair[1], first, second),
    )
}

fn virtual_executable_count(
    topology: TopologyView<'_>,
    front_layer: &FrontLayerScores,
    first: (usize, usize),
    second: Option<(usize, usize)>,
) -> usize {
    if second.is_none() {
        let mut indices = Vec::<usize>::with_capacity(2);
        if let Some((index, _)) = front_layer.qubits[first.0] {
            indices.push(index);
        }
        if let Some((index, _)) = front_layer.qubits[first.1] {
            if !indices.contains(&index) {
                indices.push(index);
            }
        }
        return indices
            .into_iter()
            .filter(|&index| {
                virtual_pair_distance(topology, front_layer.nodes[index], first, None) == 1
            })
            .count();
    }
    front_layer
        .nodes
        .iter()
        .filter(|pair| virtual_pair_distance(topology, **pair, first, second) == 1)
        .count()
}

fn virtual_extended_executable_count(
    topology: TopologyView<'_>,
    extended_set: &ExtendedSetScores,
    first: (usize, usize),
) -> usize {
    extended_set
        .pairs
        .iter()
        .filter(|pair| virtual_pair_distance(topology, **pair, first, None) == 1)
        .count()
}

fn enumerate_virtual_front_swaps(
    topology: TopologyView<'_>,
    front_layer: &FrontLayerScores,
    first: (usize, usize),
) -> Vec<(usize, usize)> {
    let mut active = vec![false; topology.num_qubits()];
    for &physical in front_layer.iter_active() {
        active[moved_by_swap(physical, first)] = true;
    }

    let mut out = Vec::<(usize, usize)>::new();
    for physical in 0..active.len() {
        if !active[physical] {
            continue;
        }
        for &neighbor in topology.neighbors(physical) {
            if neighbor > physical || !active[neighbor] {
                out.push((physical, neighbor));
            }
        }
    }
    out
}

fn same_edge(a: (usize, usize), b: (usize, usize)) -> bool {
    (a.0 == b.0 && a.1 == b.1) || (a.0 == b.1 && a.1 == b.0)
}

fn choose_epsilon_best(
    scored: &[(usize, (usize, usize), f64)],
    epsilon: f64,
    rng: &mut RngState,
) -> Option<(usize, usize)> {
    let mut best_score = f64::INFINITY;
    let mut best = Vec::<(usize, usize)>::new();
    for &(_, swap, score) in scored {
        if score + epsilon < best_score {
            best_score = score;
            best.clear();
            best.push(swap);
        } else if (score - best_score).abs() <= epsilon {
            best.push(swap);
        }
    }
    if best.is_empty() {
        None
    } else {
        Some(best[rng.gen_index(best.len())])
    }
}

fn choose_release_swap(
    ctx: &SwapSelectionContext<'_>,
    front_layer: &FrontLayerScores,
    one_ply_scores: &[(usize, (usize, usize), f64)],
    epsilon: f64,
    rng: &mut RngState,
) -> Option<(usize, usize)> {
    let topology = ctx.topology();
    let farthest = front_layer
        .nodes
        .iter()
        .copied()
        .max_by_key(|pair| topology.distance(pair[0], pair[1]))?;
    let old_distance = topology.distance(farthest[0], farthest[1]);

    let mut improving = Vec::<(usize, (usize, usize), f64)>::new();
    for &(index, swap, score) in one_ply_scores {
        let new_distance = virtual_pair_distance(topology, farthest, swap, None);
        if new_distance < old_distance {
            let reversal = ctx
                .last_applied_swap()
                .map(|last| same_edge(last, swap))
                .unwrap_or(false);
            improving.push((
                index,
                swap,
                score + if reversal { 0.35 } else { 0.0 },
            ));
        }
    }

    choose_epsilon_best(&improving, epsilon, rng)
        .or_else(|| choose_epsilon_best(one_ply_scores, epsilon, rng))
}

fn choose_dense_layout_subset(
    topology: TopologyView<'_>,
    logical_component_size: usize,
    target_component: &[usize],
) -> Result<Vec<usize>, RouterError> {
    if logical_component_size > target_component.len() {
        return Err(RouterError::Routing(format!(
            "logical component size {logical_component_size} exceeds target component size {}",
            target_component.len()
        )));
    }
    if logical_component_size == target_component.len() {
        return Ok(target_component.to_vec());
    }

    let local_index = target_component
        .iter()
        .enumerate()
        .map(|(local, global)| (*global, local))
        .collect::<HashMap<usize, usize>>();
    let mut local_adj = Array2::<f64>::zeros((target_component.len(), target_component.len()));
    for &global_a in target_component {
        let a = local_index[&global_a];
        for &global_b in topology.neighbors(global_a) {
            if let Some(&b) = local_index.get(&global_b) {
                local_adj[[a, b]] = 1.0;
            }
        }
    }
    let error_matrix = Array2::<f64>::zeros((target_component.len(), target_component.len()));
    let [_, _, best_map] = dense_layout::best_subset(
        logical_component_size,
        local_adj.view(),
        0,
        0,
        false,
        true,
        error_matrix.view(),
    );
    let chosen = best_map
        .into_iter()
        .take(logical_component_size)
        .map(|local| target_component[local])
        .collect::<Vec<_>>();
    ensure_connected_subset(topology, &chosen)?;
    Ok(chosen)
}

fn ensure_connected_subset(topology: TopologyView<'_>, subset: &[usize]) -> Result<(), RouterError> {
    if subset.is_empty() {
        return Ok(());
    }
    let set = subset.iter().copied().collect::<HashSet<_>>();
    let mut seen = HashSet::<usize>::new();
    let mut queue = VecDeque::<usize>::new();
    queue.push_back(subset[0]);
    seen.insert(subset[0]);

    while let Some(node) = queue.pop_front() {
        for &next in topology.neighbors(node) {
            if set.contains(&next) && seen.insert(next) {
                queue.push_back(next);
            }
        }
    }

    if seen.len() != set.len() {
        return Err(RouterError::Routing(
            "selected layout subset is not connected".to_string(),
        ));
    }
    Ok(())
}

fn assign_components_to_target(
    logical_components: &[Vec<usize>],
    target_components: &[Vec<usize>],
) -> Result<Vec<(Vec<usize>, usize)>, RouterError> {
    if logical_components.is_empty() {
        return Ok(Vec::new());
    }

    let mut logical_sorted = logical_components.to_vec();
    logical_sorted.sort_by_key(|component| std::cmp::Reverse(component.len()));

    let mut target_sorted = target_components
        .iter()
        .enumerate()
        .map(|(idx, component)| (idx, component.len()))
        .collect::<Vec<_>>();
    target_sorted.sort_by_key(|(_, size)| std::cmp::Reverse(*size));

    let mut free_capacity = target_sorted
        .iter()
        .map(|(idx, size)| (*idx, *size))
        .collect::<HashMap<usize, usize>>();
    let mut assignments = Vec::<(Vec<usize>, usize)>::new();

    for logical in logical_sorted {
        let size = logical.len();
        let mut chosen = None;
        for (target_idx, _) in &target_sorted {
            let cap = free_capacity.get(target_idx).copied().unwrap_or(0);
            if cap >= size {
                chosen = Some(*target_idx);
                break;
            }
        }
        let Some(target_idx) = chosen else {
            return Err(RouterError::Routing(format!(
                "logical component of size {size} cannot fit any target component"
            )));
        };
        *free_capacity
            .get_mut(&target_idx)
            .expect("selected target component must exist") -= size;
        assignments.push((logical, target_idx));
    }
    Ok(assignments)
}

#[derive(Debug, Clone)]
struct LogicalInteractionModel {
    logicals: Vec<usize>,
    weights: Vec<Vec<f64>>,
    first_layer_bonus: Vec<Vec<f64>>,
    weighted_degree: Vec<f64>,
}

impl LogicalInteractionModel {
    fn from_component(circuit: CircuitDagView<'_>, logicals: &[usize]) -> Self {
        let size = logicals.len();
        let local_index = logicals
            .iter()
            .enumerate()
            .map(|(local, logical)| (*logical, local))
            .collect::<HashMap<usize, usize>>();
        let first_layer = circuit
            .first_layer_node_ids()
            .iter()
            .copied()
            .collect::<HashSet<usize>>();
        let mut weights = vec![vec![0.0; size]; size];
        let mut first_layer_bonus = vec![vec![0.0; size]; size];
        let mut two_qubit_rank = 0usize;

        for node_id in circuit.node_ids() {
            let Some((logical_a, logical_b)) = circuit.node(node_id).two_qubit_pair() else {
                continue;
            };
            let rank = two_qubit_rank;
            two_qubit_rank += 1;
            let (Some(&a), Some(&b)) =
                (local_index.get(&logical_a), local_index.get(&logical_b))
            else {
                continue;
            };
            if a == b {
                continue;
            }

            // Repetitions accumulate naturally, while the first interactions
            // receive up to three times the unit weight.
            let weight = 1.0 + 2.0 / (1.0 + (rank as f64) / 16.0);
            weights[a][b] += weight;
            weights[b][a] += weight;
            if first_layer.contains(&node_id) {
                let bonus = 0.40 * weight;
                first_layer_bonus[a][b] += bonus;
                first_layer_bonus[b][a] += bonus;
            }
        }

        let weighted_degree = weights
            .iter()
            .map(|row| row.iter().sum::<f64>())
            .collect::<Vec<_>>();
        Self {
            logicals: logicals.to_vec(),
            weights,
            first_layer_bonus,
            weighted_degree,
        }
    }

    fn pair_cost(&self, a: usize, b: usize, distance: u32) -> f64 {
        self.weights[a][b] * (distance as f64)
            - if distance == 1 {
                self.first_layer_bonus[a][b]
            } else {
                0.0
            }
    }

    fn objective(&self, topology: TopologyView<'_>, physicals: &[usize]) -> f64 {
        let mut total = 0.0;
        for a in 0..self.logicals.len() {
            for b in (a + 1)..self.logicals.len() {
                if self.weights[a][b] == 0.0 {
                    continue;
                }
                total += self.pair_cost(
                    a,
                    b,
                    topology.distance(physicals[a], physicals[b]),
                );
            }
        }
        total
    }

    fn exchange_delta(
        &self,
        topology: TopologyView<'_>,
        physicals: &[usize],
        a: usize,
        b: usize,
    ) -> f64 {
        let mut delta = 0.0;
        for other in 0..self.logicals.len() {
            if other == a || other == b {
                continue;
            }
            let old_a =
                self.pair_cost(a, other, topology.distance(physicals[a], physicals[other]));
            let old_b =
                self.pair_cost(b, other, topology.distance(physicals[b], physicals[other]));
            let new_a =
                self.pair_cost(a, other, topology.distance(physicals[b], physicals[other]));
            let new_b =
                self.pair_cost(b, other, topology.distance(physicals[a], physicals[other]));
            delta += (new_a + new_b) - (old_a + old_b);
        }
        delta
    }
}

fn interaction_aware_embedding(
    topology: TopologyView<'_>,
    model: &LogicalInteractionModel,
    subset: &[usize],
) -> Vec<usize> {
    let size = model.logicals.len();
    if size <= 1 {
        return subset.to_vec();
    }

    let subset_set = subset.iter().copied().collect::<HashSet<_>>();
    let central_distance = subset
        .iter()
        .map(|&physical| {
            subset
                .iter()
                .map(|&other| topology.distance(physical, other) as u64)
                .sum::<u64>()
        })
        .collect::<Vec<_>>();
    let induced_degree = subset
        .iter()
        .map(|&physical| {
            topology
                .neighbors(physical)
                .iter()
                .filter(|neighbor| subset_set.contains(neighbor))
                .count()
        })
        .collect::<Vec<_>>();

    let first_logical = (0..size)
        .max_by(|&left, &right| {
            model.weighted_degree[left]
                .total_cmp(&model.weighted_degree[right])
                .then_with(|| model.logicals[right].cmp(&model.logicals[left]))
        })
        .unwrap_or(0);
    let first_physical = (0..size)
        .min_by(|&left, &right| {
            central_distance[left]
                .cmp(&central_distance[right])
                .then_with(|| induced_degree[right].cmp(&induced_degree[left]))
                .then_with(|| subset[left].cmp(&subset[right]))
        })
        .unwrap_or(0);

    let mut physicals = vec![usize::MAX; size];
    let mut logical_placed = vec![false; size];
    let mut physical_used = vec![false; size];
    physicals[first_logical] = subset[first_physical];
    logical_placed[first_logical] = true;
    physical_used[first_physical] = true;

    for _ in 1..size {
        let logical = (0..size)
            .filter(|&candidate| !logical_placed[candidate])
            .max_by(|&left, &right| {
                let left_connection = (0..size)
                    .filter(|&placed| logical_placed[placed])
                    .map(|placed| model.weights[left][placed])
                    .sum::<f64>();
                let right_connection = (0..size)
                    .filter(|&placed| logical_placed[placed])
                    .map(|placed| model.weights[right][placed])
                    .sum::<f64>();
                left_connection
                    .total_cmp(&right_connection)
                    .then_with(|| {
                        model.weighted_degree[left].total_cmp(&model.weighted_degree[right])
                    })
                    .then_with(|| model.logicals[right].cmp(&model.logicals[left]))
            })
            .expect("an unplaced logical must remain");

        let physical_index = (0..size)
            .filter(|&candidate| !physical_used[candidate])
            .min_by(|&left, &right| {
                let left_cost = (0..size)
                    .filter(|&placed| logical_placed[placed])
                    .map(|placed| {
                        model.pair_cost(
                            logical,
                            placed,
                            topology.distance(subset[left], physicals[placed]),
                        )
                    })
                    .sum::<f64>();
                let right_cost = (0..size)
                    .filter(|&placed| logical_placed[placed])
                    .map(|placed| {
                        model.pair_cost(
                            logical,
                            placed,
                            topology.distance(subset[right], physicals[placed]),
                        )
                    })
                    .sum::<f64>();
                left_cost
                    .total_cmp(&right_cost)
                    .then_with(|| central_distance[left].cmp(&central_distance[right]))
                    .then_with(|| induced_degree[right].cmp(&induced_degree[left]))
                    .then_with(|| subset[left].cmp(&subset[right]))
            })
            .expect("an unused physical must remain");
        physicals[logical] = subset[physical_index];
        logical_placed[logical] = true;
        physical_used[physical_index] = true;
    }

    // A few deterministic passes capture useful pairwise corrections without
    // imposing quadratic-refinement costs on large circuits.
    if size <= 128 {
        for _ in 0..3 {
            let mut changed = false;
            for a in 0..size {
                for b in (a + 1)..size {
                    if model.exchange_delta(topology, &physicals, a, b) < -1e-9 {
                        physicals.swap(a, b);
                        changed = true;
                    }
                }
            }
            if !changed {
                break;
            }
        }
    }
    physicals
}

fn choose_component_embedding(
    topology: TopologyView<'_>,
    circuit: CircuitDagView<'_>,
    logical_component: &[usize],
    subset: &[usize],
) -> Vec<usize> {
    let incumbent = subset.to_vec();
    if logical_component.len() <= 1 {
        return incumbent;
    }

    let model = LogicalInteractionModel::from_component(circuit, logical_component);
    let incumbent_score = model.objective(topology, &incumbent);
    if incumbent_score <= 0.0 {
        return incumbent;
    }
    let embedded = interaction_aware_embedding(topology, &model, subset);
    let embedded_score = model.objective(topology, &embedded);

    // Static interaction distance is only a surrogate for routed SWAP count.
    // Demand a material win to retain the incumbent's useful trial diversity.
    if embedded_score + 1e-9 < incumbent_score * 0.985 {
        embedded
    } else {
        incumbent
    }
}

fn choose_disjoint_aware_layout(ctx: &InitialLayoutContext<'_>) -> Result<Vec<usize>, RouterError> {
    let circuit = ctx.circuit();
    let topology = ctx.topology();
    let num_logical = circuit.num_logical_qubits();
    let used = circuit.used_logical_qubits();
    if used.is_empty() {
        return Ok((0..num_logical).collect());
    }

    let logical_components = circuit.logical_interaction_components();
    let target_components = topology.connected_components();
    if target_components.is_empty() {
        return Err(RouterError::Routing(
            "topology has no connected components".to_string(),
        ));
    }

    let assignments = assign_components_to_target(logical_components, target_components)?;
    let mut mapping = vec![usize::MAX; num_logical];
    let mut used_physical = vec![false; topology.num_qubits()];

    for (logical_component, target_component_idx) in assignments {
        if logical_component.is_empty() {
            continue;
        }
        let target_component = &target_components[target_component_idx];
        let local = choose_dense_layout_subset(topology, logical_component.len(), target_component)?;
        let component_mapping =
            choose_component_embedding(topology, circuit, &logical_component, &local);
        for (logical, physical) in logical_component.iter().zip(component_mapping) {
            mapping[*logical] = physical;
            used_physical[physical] = true;
        }
    }

    let mut free_physical = (0..topology.num_qubits()).filter(|q| !used_physical[*q]);
    for slot in &mut mapping {
        if *slot == usize::MAX {
            *slot = free_physical.next().ok_or_else(|| {
                RouterError::Routing("not enough physical qubits to complete layout".to_string())
            })?;
        }
    }
    Ok(mapping)
}

#[derive(Debug, Clone)]
pub struct CandidatePolicy {
    pub basic_weight: f64,
    pub lookahead_weight: f64,
    pub lookahead_size: usize,
    pub set_scaling: SetScaling,
    pub use_decay: bool,
    pub decay_increment: f64,
    pub decay_reset: usize,
    pub best_epsilon: f64,
    decay_state: Vec<f64>,
}

impl Default for CandidatePolicy {
    fn default() -> Self {
        Self {
            basic_weight: 1.0,
            lookahead_weight: 0.5,
            lookahead_size: 20,
            set_scaling: SetScaling::Size,
            use_decay: true,
            decay_increment: 0.001,
            decay_reset: 5,
            best_epsilon: 1e-10,
            decay_state: Vec::new(),
        }
    }
}

impl CandidatePolicy {
    fn refresh_decay_state(&mut self, ctx: &SwapSelectionContext<'_>) {
        if !self.use_decay {
            return;
        }

        let num_qubits = ctx.topology().num_qubits();
        if self.decay_state.len() != num_qubits {
            self.decay_state = vec![1.0; num_qubits];
        }

        if ctx.swaps_since_progress() == 0 {
            self.decay_state.fill(1.0);
            return;
        }

        let Some((a, b)) = ctx.last_applied_swap() else {
            return;
        };
        let reset = self.decay_reset.max(1);
        if ctx.swaps_since_progress() % reset == 0 {
            self.decay_state.fill(1.0);
        } else {
            self.decay_state[a] += self.decay_increment;
            self.decay_state[b] += self.decay_increment;
        }
    }
}

impl Policy for CandidatePolicy {
    fn choose_best_initial_layout(
        &mut self,
        ctx: &InitialLayoutContext<'_>,
        _rng: &mut RngState,
    ) -> Result<Vec<usize>, RouterError> {
        self.decay_state.clear();
        choose_disjoint_aware_layout(ctx)
    }

    fn choose_best_swap(
        &mut self,
        ctx: &SwapSelectionContext<'_>,
        rng: &mut RngState,
    ) -> Option<(usize, usize)> {
        self.refresh_decay_state(ctx);

        let front_layer = FrontLayerScores::from_ctx(ctx);
        let candidates = enumerate_candidate_swaps(ctx.topology(), &front_layer);
        if candidates.is_empty() {
            return None;
        }

        let extended_set = build_extended_set(ctx, self.lookahead_size);

        let scale = |weight: f64, size: usize, scaling: SetScaling| -> f64 {
            match scaling {
                SetScaling::Constant => weight,
                SetScaling::Size => {
                    if size == 0 {
                        0.0
                    } else {
                        weight / (size as f64)
                    }
                }
            }
        };

        let basic_weight = scale(self.basic_weight, front_layer.len(), self.set_scaling);
        let lookahead_weight = scale(
            self.lookahead_weight,
            extended_set.len(),
            self.set_scaling,
        );

        let mut swap_scores = candidates
            .iter()
            .copied()
            .map(|swap| (swap, 0.0))
            .collect::<Vec<_>>();

        let mut absolute_score = 0.0;
        absolute_score += basic_weight * front_layer.total_score(ctx.topology());
        for (swap, score) in &mut swap_scores {
            *score += basic_weight * front_layer.score_delta(*swap, ctx.topology());
        }

        if !extended_set.is_empty() && self.lookahead_weight != 0.0 {
            absolute_score += lookahead_weight * extended_set.total_score(ctx.topology());
            for (swap, score) in &mut swap_scores {
                *score += lookahead_weight * extended_set.score_delta(*swap, ctx.topology());
            }
        }

        if self.use_decay {
            for (swap, score) in &mut swap_scores {
                *score =
                    (absolute_score + *score) * self.decay_state[swap.0].max(self.decay_state[swap.1]);
            }
        } else {
            for (_, score) in &mut swap_scores {
                *score = absolute_score + *score;
            }
        }

        let one_ply_scores = swap_scores
            .iter()
            .enumerate()
            .map(|(index, (swap, score))| (index, *swap, *score))
            .collect::<Vec<_>>();

        // Executing a ready gate changes the DAG frontier, which a virtual
        // second ply cannot model.  Prefer such moves immediately and use the
        // beam only for genuine setup decisions.
        let max_executable = candidates
            .iter()
            .map(|&swap| {
                virtual_executable_count(ctx.topology(), &front_layer, swap, None)
            })
            .max()
            .unwrap_or(0);
        if max_executable > 0 {
            let mut immediate = Vec::<(usize, (usize, usize), f64)>::new();
            for (index, &swap) in candidates.iter().enumerate() {
                if virtual_executable_count(ctx.topology(), &front_layer, swap, None)
                    != max_executable
                {
                    continue;
                }
                let decay = if self.use_decay {
                    self.decay_state[swap.0].max(self.decay_state[swap.1])
                } else {
                    1.0
                };
                let reversal_penalty = ctx
                    .last_applied_swap()
                    .map(|last| if same_edge(last, swap) { 0.35 } else { 0.0 })
                    .unwrap_or(0.0);
                let front_reward = 0.55 * basic_weight * (max_executable as f64);
                let lookahead_reward = 0.18
                    * lookahead_weight
                    * (virtual_extended_executable_count(
                        ctx.topology(),
                        &extended_set,
                        swap,
                    ) as f64);
                immediate.push((
                    index,
                    swap,
                    one_ply_scores[index].2 - (front_reward + lookahead_reward) * decay
                        + reversal_penalty,
                ));
            }
            return choose_epsilon_best(&immediate, self.best_epsilon, rng);
        }

        const RELEASE_THRESHOLD: usize = 6;
        if ctx.swaps_since_progress() >= RELEASE_THRESHOLD {
            return choose_release_swap(
                ctx,
                &front_layer,
                &one_ply_scores,
                self.best_epsilon,
                rng,
            );
        }

        let mut beam = one_ply_scores.clone();
        beam.sort_by(|left, right| {
            let left_reversal = ctx
                .last_applied_swap()
                .map(|last| same_edge(last, left.1))
                .unwrap_or(false);
            let right_reversal = ctx
                .last_applied_swap()
                .map(|last| same_edge(last, right.1))
                .unwrap_or(false);
            let left_score = left.2 + if left_reversal { 0.35 } else { 0.0 };
            let right_score = right.2 + if right_reversal { 0.35 } else { 0.0 };
            left_score.total_cmp(&right_score)
        });
        beam.truncate(6);

        let mut two_ply = Vec::<(usize, (usize, usize), f64)>::new();
        for &(index, first, _) in &beam {
            let virtual_front = front_layer.after_swap(first);
            let virtual_extended = extended_set.after_swap(first);
            let base_score = virtual_front.objective(ctx.topology(), basic_weight, 0.55)
                + virtual_extended.objective(ctx.topology(), lookahead_weight, 0.18);
            let mut seconds = enumerate_virtual_front_swaps(ctx.topology(), &front_layer, first)
                .into_iter()
                .map(|second| {
                    (
                        second,
                        base_score
                            + virtual_front.objective_delta(
                                second,
                                ctx.topology(),
                                basic_weight,
                                0.55,
                            )
                            + virtual_extended.objective_delta(
                                second,
                                ctx.topology(),
                                lookahead_weight,
                                0.18,
                            ),
                    )
                })
                .collect::<Vec<_>>();
            seconds.sort_by(|left, right| left.1.total_cmp(&right.1));
            seconds.truncate(12);

            let Some((_, best_second_score)) = seconds.first().copied() else {
                continue;
            };
            let decay = if self.use_decay {
                self.decay_state[first.0].max(self.decay_state[first.1])
            } else {
                1.0
            };
            let reversal_penalty = ctx
                .last_applied_swap()
                .map(|last| if same_edge(last, first) { 0.35 } else { 0.0 })
                .unwrap_or(0.0);
            two_ply.push((
                index,
                first,
                best_second_score * decay + 0.06 + reversal_penalty,
            ));
        }

        choose_epsilon_best(&two_ply, self.best_epsilon, rng)
            .or_else(|| choose_epsilon_best(&one_ply_scores, self.best_epsilon, rng))
    }
}
// EVOLVE-BLOCK-END
