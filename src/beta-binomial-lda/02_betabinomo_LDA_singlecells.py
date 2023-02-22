# %%
import torch
import torch.utils.data as data 
import torch.distributions as distributions

device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
print(device)

import pandas as pd
import numpy as np
import copy

from scipy.special import digamma, betaln
from scipy.sparse import coo_matrix
import matplotlib.pyplot as plt
import seaborn as sns

# %%
# load data 
#__________________________________________________________________________________________________________

class CSRDataLoader():
    
    # data loader for csr_matrix

    def __init__(self, csr_mat1, csr_mat2):
        self.csr_mat1 = csr_mat1
        self.csr_mat2 = csr_mat2
        
    def __getitem__(self, idx):
        mat1_row = self.csr_mat1[idx].toarray().reshape(-1)
        mat2_row = self.csr_mat2[idx].toarray().reshape(-1)
        return mat1_row, mat2_row
    
    def __len__(self):
        return self.csr_mat1.shape[0]

def load_cluster_data(input_file):

   # read in this pickl file output_test.pkl
    summarized_data = pd.read_pickle(input_file)
    coo = summarized_data[["cell_id_index", "junction_id_index", "junc_count", "Cluster_Counts", "Cluster", "junc_ratio"]]

    cell_ids_conversion = summarized_data[["cell_id_index", "cell_id", "cell_type"]].drop_duplicates()
    junction_ids_conversion = summarized_data[["junction_id_index", "junction_id", "Cluster"]].drop_duplicates()
    junction_ids_conversion = junction_ids_conversion.sort_values("junction_id_index")
    cell_ids_conversion = cell_ids_conversion.sort_values("cell_id_index")

    #make coo_matrix for junction counts and cluster counts 
    coo_counts_sparse = coo_matrix((coo.junc_count, (coo.cell_id_index, coo.junction_id_index)), shape=(len(coo.cell_id_index.unique()), len(coo.junction_id_index.unique())))
    coo_cluster_sparse = coo_matrix((coo.Cluster_Counts, (coo.cell_id_index, coo.junction_id_index)), shape=(len(coo.cell_id_index.unique()), len(coo.junction_id_index.unique())))

    #convert to csr_matrix format 
    coo_counts_sparse = coo_counts_sparse.tocsr()
    coo_cluster_sparse = coo_cluster_sparse.tocsr()

    return(coo_counts_sparse, coo_cluster_sparse, cell_ids_conversion, junction_ids_conversion)

# %% 

# Functions for probabilistic beta-binomial AS model 
#__________________________________________________________________________________________________________

def init_var_params(eps = 1e-9):
    
    '''
    Function for initializing variational parameters using global variables N, J, K   
    Sample variables from prior distribtuions 
    To-Do: implement SVD for more relevant initializations
    '''
    
    print('Initialize VI params')

    # Cell states distributions , each junction in the FULL list of junctions get a ALPHA and PI parameter for each cell state
    ALPHA = torch.from_numpy(np.random.uniform(0.01, 0.05, size=(J, K))) 
    PI = torch.from_numpy(np.random.uniform(100, 1000, size=(J, K)))

    # Topic Proportions (cell states proportions), GAMMA ~ Dirichlet(eta) 
    GAMMA = torch.ones(N, K, dtype=torch.float64) * 100

    # Cell State Assignments, each cell gets a PHI value for each of its junctions
    PHI = torch.rand((N, J, K)).double() + eps
    PHI = PHI / PHI.sum(dim=-1, keepdim=True) # normalize to sum to 1
    
    return ALPHA, PI, GAMMA, PHI

# %%

# Functions for calculating the ELBO
#__________________________________________________________________________________________________________

def E_log_pbeta(ALPHA, PI, alpha_prior=0.65, pi_prior=0.65):
    '''
    Expected log joint of our latent variable B ~ Beta(a, b)
    Calculated here in terms of its variational parameters ALPHA and PI 
    ALPHA = K x J matrix 
    PI = K x J matrix 
    alpha_prior and pi_prior = scalar are fixed priors on the parameters of the beta distribution
    '''

    E_log_p_beta_a = torch.sum(((alpha_prior -1 )  * (digamma(ALPHA) - digamma(ALPHA + PI))))
    E_log_p_beta_b = torch.sum(((pi_prior-1) * (digamma(PI) - digamma(ALPHA + PI))))

    E_log_pB = E_log_p_beta_a + E_log_p_beta_b
    assert(~(np.isnan(E_log_pB)))
    return(E_log_pB)


def E_log_ptheta(GAMMA, eta=0.1):
    
    '''
    We are assigning a K vector to each cell that has the proportion of each K present in each cell 
    GAMMA is a variational parameter assigned to each cell, follows a K dirichlet
    '''

    E_log_p_theta = torch.sum((eta - 1) * sum(digamma(GAMMA).T - digamma(torch.sum(GAMMA, dim=1))))
    assert(~(np.isnan(E_log_p_theta)))
    return(E_log_p_theta)

# %%
def E_log_xz(ALPHA, PI, GAMMA, PHI):
    
    '''
    sum over N cells and J junctions... where we are looking at the exp log p(z|theta)
    plus the exp log p(x|beta and z)
    '''

    E_log_p_xz_part1 = 0
    E_log_p_xz_part2 = 0

    PHI_t = copy.deepcopy(PHI)
    GAMMA_t = copy.deepcopy(GAMMA)

    ### E[log p(Z_ij|THETA_i)]    
    all_digammas = (digamma(GAMMA_t) - digamma(GAMMA_t.sum(dim=1)).unsqueeze(1)) # shape: (N, K)
    E_log_p_xz_part1 += sum(torch.sum(PHI_t[c] @ all_digammas[c]) for c in range(N)) #
    
    ### E[log p(Y_ij | BETA, Z_ij)] BETA here defines our probability of success for every junction given a cell state
    lnBeta = (digamma(ALPHA) - digamma(ALPHA + PI)).unsqueeze(0) # shape: (1, J, K)
    ln1mBeta = digamma(PI) - digamma(ALPHA + PI).unsqueeze(0) # shape: (1, J, K)

    for c, (juncs, clusters) in enumerate(cell_junc_counts): #cell_junc_counts is a dataloader object

        # Broadcast the lnBeta tensor along the batch dimension
        lnBeta_t = lnBeta.expand(juncs.size(0), -1, -1)  # shape: (32, J, K) where 32 is batch size
        ln1mBeta_t = ln1mBeta.expand(juncs.size(0), -1, -1)  # shape: (32, J, K) where 32 is batch size

        # Compute the index range for the current batch
        phi_batch = PHI[c:c+juncs.size(0)]  # shape: (batch_size, J, K)

        # Perform the element-wise multiplication - i think these steps take the longest
        second_term = torch.mul(lnBeta_t, juncs.unsqueeze(-1)).squeeze(-1)
        third_term = torch.mul(ln1mBeta_t, (clusters-juncs).unsqueeze(-1)).squeeze(-1)
        E_log_p_xz_part2 += torch.sum(phi_batch * (second_term + third_term))
    
    E_log_p_xz = E_log_p_xz_part1 + E_log_p_xz_part2
    return(E_log_p_xz)

# %%

## Define all the entropies
#__________________________________________________________________________________________________________

def get_entropy(ALPHA, PI, GAMMA, PHI):
    
    '''
    H(X) = E(-logP(X)) for random variable X whose pdf is P
    '''

    #1. Sum over Js, entropy of beta distribution for each K given its variational parameters     
    beta_dist = distributions.Beta(ALPHA, PI)
    E_log_q_beta = beta_dist.entropy().mean(dim=1).sum()
    print(E_log_q_beta)
    #2. Sum over all cells, entropy of dirichlet cell state proportions given its variational parameter 
    dirichlet_dist = distributions.Dirichlet(GAMMA)
    E_log_q_theta = dirichlet_dist.entropy().sum()
    print(E_log_q_theta)
    #3. Sum over all cells and junctions, entropy of  categorical PDF given its variational parameter (PHI_ij)
    E_log_q_z = -(PHI * torch.log(PHI)).sum(dim=-1).sum()
    print(E_log_q_z)
    entropy_term = E_log_q_beta + E_log_q_theta + E_log_q_z
    return entropy_term

# %%

def get_elbo(ALPHA, PI, GAMMA, PHI):
    
    #1. Calculate expected log joint
    E_log_pbeta_val = E_log_pbeta(ALPHA, PI)
    print("E_log_pbeta_val" + str(E_log_pbeta_val))
    E_log_ptheta_val = E_log_ptheta(GAMMA)
    print("E_log_ptheta_val" + str(E_log_ptheta_val))
    E_log_pzx_val = E_log_xz(ALPHA, PI, GAMMA, PHI) #**this step takes a long time
    print("E_log_pzx_val" + str(E_log_pzx_val)) 

    #2. Calculate entropy
    entropy = get_entropy(ALPHA, PI, GAMMA, PHI)
    print("entropy" + str(entropy))

    #3. Calculate ELBO
    elbo = E_log_pbeta_val + E_log_ptheta_val + E_log_pzx_val + entropy
    
    print('ELBO: {}'.format(elbo))
    return(elbo)


# %%

def compute_logsumexp(tensor):
    max_value = torch.max(tensor, dim=-1, keepdim=True)[0]
    numerator = torch.exp(tensor - max_value)
    denominator = torch.sum(numerator, dim=-1, keepdim=True)
    logsum = torch.log(denominator) + max_value
    return logsum

# define CAVI updates 
#__________________________________________________________________________________________________________

def update_z_theta(ALPHA, PI, GAMMA, PHI, theta_prior=0.1):

    '''
    Update variational parameters for z and theta distributions
    '''                
    GAMMA_t = copy.copy(GAMMA)
    ALPHA_t = copy.copy(ALPHA)
    PI_t = copy.copy(PI)
    
    #reset PHI_t for update
    PHI_t = torch.ones((N, J, K)).double() / K

    # Iterate through each cell 
    for c, (juncs, clusters) in enumerate(cell_junc_counts):
        
        batch_size = juncs.size(0)

        # Get GAMMA for that cell and for this iteration so that PHI can be updated 
        GAMMA_i_t = copy.deepcopy(GAMMA_t[c:c+batch_size]) # K-vector 
        
        # Update PHI_c to PHI_c+batch.size
        ALPHA_t_iter = ALPHA_t.expand(juncs.size(0), -1, -1)  # shape: (32, 3624, 2) where 32 is batch size
        PI_t_iter = PI_t.expand(juncs.size(0), -1, -1)  # shape: (32, 3624, 2) where 32 is batch size

        #these multiplications take the longest
        part1 = digamma(GAMMA_i_t)-digamma(torch.sum(GAMMA_i_t)).unsqueeze(0) #K long vector                  
        part2 = torch.mul(juncs.unsqueeze(-1), (digamma(ALPHA_t_iter) - digamma(ALPHA_t_iter+PI_t_iter))).squeeze(-1) #this operation might take a while
        part3 = torch.mul((clusters-juncs).unsqueeze(-1), (digamma(PI_t_iter) - digamma(ALPHA_t_iter+PI_t_iter))).squeeze(-1)  #this operation might take a while
        
        part1_expanded = part1.unsqueeze(1).expand(-1, part2.shape[1], -1)  # shape: (32, 3624, 2)
        log_PHI_i = part1_expanded + part2 + part3
        PHI_i = np.exp(log_PHI_i - compute_logsumexp(log_PHI_i))
        PHI_t[c:c+batch_size] = PHI_i 
    
        # Reset GAMMA before update
        GAMMA_t = torch.ones(N, K, dtype=torch.float64) * 100
        #print(GAMMA_t[c:c+batch_size])
        
        # Update GAMMA_c using the updated PHI_c
        GAMMA_i_t = theta_prior + torch.sum(PHI_t[c:c+batch_size], axis=1)
        GAMMA_t[c:c+batch_size] = GAMMA_i_t    
        #print(GAMMA_t[c:c+batch_size])

    return(PHI_t, GAMMA_t)    

def update_beta(PHI, alpha_prior=0.65, beta_prior=0.65):
    
    '''
    Update variational parameters for beta distribution
    '''
    
    # Iterate through each cell 
    PHI_t = copy.deepcopy(PHI)
    ALPHA_t = torch.ones((J, K)) * alpha_prior
    PI_t = torch.ones((J, K)) * beta_prior
    for c, (juncs, clusters) in enumerate(cell_junc_counts):
        batch_size = juncs.size(0)
        print(c, batch_size)
        phi_values = PHI_t[c:c+batch_size]
        # since most junction counts are zeroes should be ending up with a lot of zeros in the sum every time
        ALPHA_t += torch.sum(alpha_prior + (juncs.unsqueeze(-1) * phi_values), dim=0) #removed prior outside sum
        PI_t +=  torch.sum(beta_prior + ((clusters-juncs).unsqueeze(-1) * phi_values), dim=0) #removed prior outside sum   
    
    return(ALPHA_t, PI_t)

# %%   

def update_variational_parameters(ALPHA, PI, GAMMA, PHI):
    
    '''
    Update variational parameters for beta, theta and z distributions
    '''
    
    PHI_up, GAMMA_up = update_z_theta(ALPHA, PI, GAMMA, PHI) #slowest part#*****
    print("got the z and theta updates")
    ALPHA_up, PI_up = update_beta(PHI_up)
    print("got the beta updates")

    return(ALPHA_up, PI_up, GAMMA_up, PHI_up)

# %%

def calculate_CAVI(num_iterations=5):
    
    '''
    Calculate CAVI
    '''

    ALPHA_init, PI_init, GAMMA_init, PHI_init = init_var_params()
    elbo = get_elbo(ALPHA_init, PI_init, GAMMA_init, PHI_init)
    ALPHA_vi, PI_vi, GAMMA_vi, PHI_vi = update_variational_parameters(ALPHA_init, PI_init, GAMMA_init, PHI_init)
    print("got the first round of updates")
    elbos = []
    elbo = get_elbo(ALPHA_vi, PI_vi, GAMMA_vi, PHI_vi)
    elbos.append(elbo)
    for i in range(num_iterations):
        print(i)
        ALPHA_vi, PI_vi, GAMMA_vi, PHI_vi = update_variational_parameters(ALPHA_vi, PI_vi, GAMMA_vi, PHI_vi)
        elbo = get_elbo(ALPHA_vi, PI_vi, GAMMA_vi, PHI_vi)
        elbos.append(elbo)
        
    return(ALPHA_vi, PI_vi, GAMMA_vi, PHI_vi, elbos)

# %%
if __name__ == "__main__":

    # Load data and define global variables 
    # get input data (this is standard output from leafcutter-sc pipeline so the column names will always be the same)
    
    input_file = '/gpfs/commons/home/kisaev/leafcutter-sc/src/beta-binomial-lda/junctions_full_for_LDA.pkl' #this should be an argument that gets fed in
    coo_counts_sparse, coo_cluster_sparse, cell_ids_conversion, junction_ids_conversion = load_cluster_data(input_file)

    batch_size = 256 #should also be an argument that gets fed in
    
    #prep dataloader for training
    
    #choose random indices to subset coo_counts_sparse and coo_cluster_sparse
    rand_ind = np.random.choice(60000, size=1024, replace=False)

    cell_junc_counts = data.DataLoader(CSRDataLoader(coo_counts_sparse[rand_ind], coo_cluster_sparse[rand_ind, ]), batch_size=batch_size, shuffle=True)

    # global variables
    N = len(cell_junc_counts.dataset) # number of cells
    J = cell_junc_counts.dataset[0][0].shape[0] # number of junctions
    K = 3 # should also be an argument that gets fed in
    num_trials = 1 # should also be an argument that gets fed in
    num_iters = 5 # should also be an argument that gets fed in

    # loop over the number of trials (for now just testing using one trial but in general need to evaluate how performance is affected by number of trials)
    for t in range(num_trials):
        
        # run coordinate ascent VI
        print(K)

        ALPHA_f, PI_f, GAMMA_f, PHI_f, elbos_all = calculate_CAVI(num_iters)
        juncs_probs = ALPHA_f / (ALPHA_f+PI_f)
        theta_f = distributions.Dirichlet(GAMMA_f).sample().numpy()
        z_f = distributions.Categorical(PHI_f).sample()
        #pdb.set_trace()

        #for k in range(K):
        #    col_data = juncs_probs[:, k]
        #    plt.hist(col_data, bins=10)  # Customize the number of bins as desired
        #    plt.title(f"Histogram of column {k}")
        #    plt.show()

        #make theta_f a dataframe 
        theta_f_plot = pd.DataFrame(theta_f)
        theta_f_plot['cell_id'] = cell_ids_conversion["cell_type"].to_numpy()[rand_ind]

        celltypes = theta_f_plot.pop("cell_id")
        lut = dict(zip(celltypes.unique(), ["r", "b", "g", "orange", "purple", "brown", "pink", "gray", "olive", "cyan"]))
        print(lut)
        row_colors = celltypes.map(lut)
        sns.clustermap(theta_f_plot, row_colors=row_colors)
# %%
#sort GAMMA_f 
GAMMA_f_sort = GAMMA_f.sort(dim=1, descending=True)

# Calculate the variances for each of the 3624 junctions
variances = (ALPHA_f * PI_f) / ((ALPHA_f + PI_f) ** 2 * (ALPHA_f + PI_f + 1))
variances = variances.reshape(J, K)

# what if we initialize the cell states to be the cell types 
# batch size shouldn't affect results right? 
# seems like it's learning similar success of probability of junctions being expressed in every cell state

#If you're interested in exploring this further, you can try visualizing the data for that particular 
# junction in each cell state to see if there are any obvious differences in the distributions. You can also 
# try fitting other distributions to the data to see if they provide a better fit, or investigate if there are 
# any other variables that may be driving the differences in the variances.

#TO-DO: double check junction counts, maybe only include cell that have at least X counts so that can actually learn something about them
#clusters should have X counts overall across all cells 
#maybe just need to start out with a smaller number of junction/clusters
