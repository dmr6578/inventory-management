<template>
  <div class="restocking">
    <div class="page-header">
      <h2>{{ t('restocking.title') }}</h2>
      <p>{{ t('restocking.description') }}</p>
    </div>

    <div v-if="loading" class="loading">{{ t('common.loading') }}</div>
    <div v-else-if="error" class="error">{{ error }}</div>
    <div v-else>
      <div v-if="candidates.length === 0" class="card">
        <p class="empty-state">{{ t('restocking.emptyState') }}</p>
      </div>

      <div v-else>
        <!-- Success state replaces the interactive form after a successful submission -->
        <div v-if="submittedOrder" class="card success-card">
          <h3 class="success-title">{{ t('restocking.orderSuccess') }}</h3>
          <p class="success-detail">
            <strong>{{ submittedOrder.order_number }}</strong>
            &mdash; {{ currencySymbol }}{{ submittedOrder.total_cost.toLocaleString() }}
          </p>
          <div class="success-actions">
            <router-link to="/orders" class="btn-primary">{{ t('restocking.viewInOrders') }}</router-link>
            <button class="btn-secondary" @click="startNewOrder">{{ t('restocking.newOrder') }}</button>
          </div>
        </div>

        <template v-else>
          <div class="card budget-card">
            <div class="budget-row">
              <label class="budget-label" for="budget-slider">{{ t('restocking.budgetLabel') }}</label>
              <span class="budget-value">{{ currencySymbol }}{{ budget.toLocaleString() }}</span>
            </div>
            <input
              id="budget-slider"
              v-model.number="budget"
              type="range"
              min="0"
              :max="maxBudget"
              step="100"
              class="budget-slider"
            />
          </div>

          <div class="card">
            <div class="card-header">
              <h3 class="card-title">{{ t('restocking.title') }} ({{ candidates.length }})</h3>
            </div>
            <div class="table-container">
              <table class="restock-table">
                <thead>
                  <tr>
                    <th class="col-check"></th>
                    <th>{{ t('restocking.table.sku') }}</th>
                    <th>{{ t('restocking.table.name') }}</th>
                    <th>{{ t('restocking.table.category') }}</th>
                    <th>{{ t('restocking.table.warehouse') }}</th>
                    <th>{{ t('restocking.table.stock') }}</th>
                    <th>{{ t('restocking.table.reorderPoint') }}</th>
                    <th>{{ t('restocking.table.trend') }}</th>
                    <th>{{ t('restocking.table.forecastedDemand') }}</th>
                    <th>{{ t('restocking.table.recommendedQuantity') }}</th>
                    <th>{{ t('restocking.table.unitCost') }}</th>
                    <th>{{ t('restocking.table.subtotal') }}</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="item in candidates" :key="item.sku">
                    <td class="col-check">
                      <input
                        type="checkbox"
                        v-model="selections[item.sku].included"
                      />
                    </td>
                    <td><strong>{{ item.sku }}</strong></td>
                    <td>
                      <span v-if="item.urgent" class="badge danger urgent-badge">{{ t('restocking.urgent') }}</span>
                      {{ translateProductName(item.name) }}
                    </td>
                    <td>{{ translateCategory(item.category) }}</td>
                    <td>{{ translateWarehouse(item.warehouse) }}</td>
                    <td :class="{ 'below-reorder': item.quantity_on_hand < item.reorder_point }">
                      {{ item.quantity_on_hand }}
                    </td>
                    <td>{{ item.reorder_point }}</td>
                    <td>
                      <span :class="['badge', item.trend]">{{ t(`trends.${item.trend}`) }}</span>
                    </td>
                    <td>{{ item.forecasted_demand }}</td>
                    <td>
                      <input
                        type="number"
                        min="1"
                        class="qty-input"
                        :disabled="!selections[item.sku].included"
                        v-model.number="selections[item.sku].quantity"
                        @change="normalizeQuantity(item)"
                      />
                    </td>
                    <td>{{ currencySymbol }}{{ item.unit_cost.toLocaleString() }}</td>
                    <td>
                      <strong>{{ currencySymbol }}{{ rowSubtotal(item).toLocaleString() }}</strong>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>

          <div class="card summary-card">
            <div class="summary-row">
              <span class="summary-label">{{ t('restocking.selectedTotal') }}</span>
              <span :class="['summary-value', { 'over-budget': exceedsBudget }]">
                {{ currencySymbol }}{{ selectedTotal.toLocaleString() }}
                <span class="summary-of-budget">/ {{ currencySymbol }}{{ budget.toLocaleString() }} {{ t('restocking.ofBudget') }}</span>
              </span>
            </div>
            <p v-if="exceedsBudget" class="error">{{ t('restocking.exceedsBudget') }}</p>
            <p v-if="submitError" class="error">{{ submitError }}</p>
            <button
              class="btn-primary place-order-btn"
              :disabled="!canSubmit"
              @click="submitOrder"
            >
              {{ submitting ? t('restocking.placingOrder') : t('restocking.placeOrder') }}
            </button>
          </div>
        </template>
      </div>
    </div>
  </div>
</template>

<script>
import { ref, computed, watch, onMounted } from 'vue'
import { api } from '../api'
import { useI18n } from '../composables/useI18n'

export default {
  name: 'Restocking',
  setup() {
    const { t, currentCurrency, translateProductName, translateWarehouse } = useI18n()

    const currencySymbol = computed(() => (currentCurrency.value === 'JPY' ? '¥' : '$'))

    // Same category map as Inventory.vue - category strings from the API are always
    // the English canonical form, so translate them for display in Japanese mode.
    const translateCategory = (category) => {
      const categoryMap = {
        'Circuit Boards': t('categories.circuitBoards'),
        'Sensors': t('categories.sensors'),
        'Actuators': t('categories.actuators'),
        'Controllers': t('categories.controllers'),
        'Power Supplies': t('categories.powerSupplies')
      }
      return categoryMap[category] || category
    }

    const loading = ref(true)
    const error = ref(null)
    const candidates = ref([])

    const budget = ref(0)
    const selections = ref({})

    const submitting = ref(false)
    const submitError = ref(null)
    const submittedOrder = ref(null)

    // Slider ceiling: sum of every candidate's recommended cost. Falls back to a sane
    // default when there are no candidates yet (still loading) or the sum is 0.
    const maxBudget = computed(() => {
      const total = candidates.value.reduce((sum, c) => sum + c.recommended_cost, 0)
      return total > 0 ? total : 10000
    })

    // Replicates the backend's greedy budget-fill algorithm on the client so we don't have
    // to re-hit /restock/recommendations on every slider tick. The candidate list is already
    // sorted by the backend in priority order (urgent first, then recommended_quantity desc),
    // so all we do here is walk it in order and mark items included while they still fit.
    const buildDefaultSelections = (budgetValue) => {
      const result = {}
      let runningTotal = 0
      for (const item of candidates.value) {
        const included = runningTotal + item.recommended_cost <= budgetValue
        if (included) runningTotal += item.recommended_cost
        result[item.sku] = {
          included,
          quantity: item.recommended_quantity
        }
      }
      return result
    }

    const resetSelections = () => {
      selections.value = buildDefaultSelections(budget.value)
    }

    // Budget changes wipe any manual checkbox/quantity edits and reapply the greedy default.
    // Deliberately simple - not attempting to preserve manual overrides across slider moves,
    // which would be over-engineering for a list this small.
    watch(budget, resetSelections)

    const loadCandidates = async () => {
      loading.value = true
      error.value = null
      try {
        // Fetch once with budget=0 - the endpoint always returns every restockable item
        // regardless of budget; budget only affects which are flagged default_selected server-side.
        candidates.value = await api.getRestockRecommendations(0)
        // Default the slider to cover every recommendation so the page is immediately useful.
        budget.value = maxBudget.value
        resetSelections()
      } catch (err) {
        error.value = 'Failed to load restock recommendations'
        console.error(err)
      } finally {
        loading.value = false
      }
    }

    const rowSubtotal = (item) => {
      const sel = selections.value[item.sku]
      if (!sel) return 0
      const qty = Number(sel.quantity) || 0
      return qty * item.unit_cost
    }

    const normalizeQuantity = (item) => {
      const sel = selections.value[item.sku]
      if (!sel) return
      if (!Number.isFinite(sel.quantity) || sel.quantity < 1) {
        sel.quantity = 1
      }
    }

    const selectedTotal = computed(() => {
      return candidates.value.reduce((sum, item) => {
        const sel = selections.value[item.sku]
        if (sel && sel.included) {
          return sum + (Number(sel.quantity) || 0) * item.unit_cost
        }
        return sum
      }, 0)
    })

    const exceedsBudget = computed(() => selectedTotal.value > budget.value)

    const hasSelection = computed(() => {
      return Object.values(selections.value).some((sel) => sel.included)
    })

    const canSubmit = computed(() => hasSelection.value && !exceedsBudget.value && !submitting.value)

    const submitOrder = async () => {
      submitting.value = true
      submitError.value = null
      try {
        const items = candidates.value
          .filter((item) => selections.value[item.sku]?.included)
          .map((item) => ({ sku: item.sku, quantity: Number(selections.value[item.sku].quantity) || 0 }))

        const order = await api.createRestockOrder({ budget: budget.value, items })
        submittedOrder.value = order
      } catch (err) {
        submitError.value = err.response?.data?.detail || 'Failed to place order'
        console.error(err)
      } finally {
        submitting.value = false
      }
    }

    const startNewOrder = () => {
      submittedOrder.value = null
      submitError.value = null
      loadCandidates()
    }

    onMounted(loadCandidates)

    return {
      t,
      currencySymbol,
      translateProductName,
      translateWarehouse,
      translateCategory,
      loading,
      error,
      candidates,
      budget,
      maxBudget,
      selections,
      submitting,
      submitError,
      submittedOrder,
      rowSubtotal,
      normalizeQuantity,
      selectedTotal,
      exceedsBudget,
      canSubmit,
      submitOrder,
      startNewOrder
    }
  }
}
</script>

<style scoped>
.empty-state {
  color: #64748b;
  font-size: 0.938rem;
  text-align: center;
  padding: 1.5rem 0;
}

.budget-card {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}

.budget-row {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
}

.budget-label {
  font-size: 0.875rem;
  font-weight: 600;
  color: #475569;
  text-transform: uppercase;
  letter-spacing: 0.05em;
}

.budget-value {
  font-size: 1.5rem;
  font-weight: 700;
  color: #0f172a;
}

.budget-slider {
  width: 100%;
  accent-color: #2563eb;
}

.restock-table {
  table-layout: auto;
}

.col-check {
  width: 40px;
}

.urgent-badge {
  margin-right: 0.5rem;
}

.below-reorder {
  color: #dc2626;
  font-weight: 600;
}

.qty-input {
  width: 80px;
  padding: 0.375rem 0.5rem;
  border: 1px solid #e2e8f0;
  border-radius: 6px;
  font-size: 0.875rem;
}

.qty-input:disabled {
  background: #f8fafc;
  color: #94a3b8;
}

.summary-card {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}

.summary-row {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
}

.summary-label {
  font-size: 0.875rem;
  font-weight: 600;
  color: #475569;
  text-transform: uppercase;
  letter-spacing: 0.05em;
}

.summary-value {
  font-size: 1.5rem;
  font-weight: 700;
  color: #0f172a;
}

.summary-value.over-budget {
  color: #dc2626;
}

.summary-of-budget {
  font-size: 0.875rem;
  font-weight: 500;
  color: #64748b;
  margin-left: 0.5rem;
}

.btn-primary {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: #2563eb;
  color: white;
  border: none;
  padding: 0.625rem 1.25rem;
  border-radius: 8px;
  font-size: 0.938rem;
  font-weight: 600;
  cursor: pointer;
  text-decoration: none;
  transition: background 0.2s ease;
}

.btn-primary:hover:not(:disabled) {
  background: #1d4ed8;
}

.btn-primary:disabled {
  background: #cbd5e1;
  cursor: not-allowed;
}

.btn-secondary {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: white;
  color: #334155;
  border: 1px solid #e2e8f0;
  padding: 0.625rem 1.25rem;
  border-radius: 8px;
  font-size: 0.938rem;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.2s ease;
}

.btn-secondary:hover {
  border-color: #cbd5e1;
  background: #f8fafc;
}

.place-order-btn {
  align-self: flex-start;
}

.success-card {
  border-left: 4px solid #10b981;
}

.success-title {
  font-size: 1.125rem;
  font-weight: 700;
  color: #059669;
  margin-bottom: 0.5rem;
}

.success-detail {
  color: #334155;
  font-size: 0.938rem;
  margin-bottom: 1rem;
}

.success-actions {
  display: flex;
  gap: 0.75rem;
}
</style>
