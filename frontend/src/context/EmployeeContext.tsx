// Simulated "who am I". NOT authentication — just a selection kept in localStorage.
import { createContext, useContext, useEffect, useState, type ReactNode } from 'react'
import { api } from '../api'
import type { Employee } from '../types'

interface Ctx {
  employees: Employee[]
  employee: Employee | null
  setEmployeeId: (id: string) => void
}

const EmployeeContext = createContext<Ctx>({ employees: [], employee: null, setEmployeeId: () => {} })
const STORAGE_KEY = 'dcc.employeeId'

function readStored(): string | null {
  try {
    return localStorage.getItem(STORAGE_KEY)
  } catch {
    return null
  }
}

export function EmployeeProvider({ children }: { children: ReactNode }) {
  const [employees, setEmployees] = useState<Employee[]>([])
  const [employeeId, setId] = useState<string | null>(readStored)

  useEffect(() => {
    api.employees().then(setEmployees).catch(() => setEmployees([]))
  }, [])

  const setEmployeeId = (id: string) => {
    setId(id)
    try {
      localStorage.setItem(STORAGE_KEY, id)
    } catch {
      /* ignore */
    }
  }

  const employee = employees.find((e) => e.id === employeeId) ?? null
  return <EmployeeContext.Provider value={{ employees, employee, setEmployeeId }}>{children}</EmployeeContext.Provider>
}

export const useEmployee = () => useContext(EmployeeContext)
